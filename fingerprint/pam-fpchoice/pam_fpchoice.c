#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <stdarg.h>
#include <linux/uinput.h>
#include <pwd.h>
#include <pthread.h>
#include <security/pam_ext.h>
#include <security/pam_modules.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/wait.h>
#include <syslog.h>
#include <systemd/sd-bus.h>
#include <time.h>
#include <unistd.h>

/* Sent by the shell helper when the reader matches. It is not a password. */
static const char MATCH_MARK[] = "\x1e";

struct auth_ctx {
    pam_handle_t *pamh;
    char *user;
    uid_t uid;
    gid_t gid;
    atomic_int stop;
    atomic_int matched;
    atomic_int prompt_active;
    pthread_t thread;
    int have_thread;
    char *device;
    sd_bus *bus;
    int claimed;
    int verify_started;
    int tries;
    int need_restart;
    int verify_dead;
};

static void wipe(char *text)
{
    if (!text)
        return;
    explicit_bzero(text, strlen(text));
    free(text);
}

static int bus_call(sd_bus *bus, const char *path, const char *member,
                    sd_bus_message **reply, const char *types, ...)
{
    sd_bus_error error = SD_BUS_ERROR_NULL;
    sd_bus_message *msg = NULL;
    va_list ap;
    int r;

    r = sd_bus_message_new_method_call(bus, &msg, "net.reactivated.Fprint",
                                       path, "net.reactivated.Fprint.Device",
                                       member);
    if (r < 0)
        return r;
    if (types && types[0]) {
        va_start(ap, types);
        r = sd_bus_message_appendv(msg, types, ap);
        va_end(ap);
        if (r < 0) {
            sd_bus_message_unref(msg);
            return r;
        }
    }
    r = sd_bus_call(bus, msg, 4000000, &error, reply);
    sd_bus_message_unref(msg);
    if (r < 0 && strcmp(member, "VerifyStop") != 0 && strcmp(member, "Release") != 0)
        syslog(LOG_AUTH | LOG_WARNING, "fprintd %s: %s", member,
               error.message ? error.message : strerror(-r));
    sd_bus_error_free(&error);
    return r;
}

static int on_verify(sd_bus_message *msg, void *userdata, sd_bus_error *error)
{
    struct auth_ctx *ctx = userdata;
    const char *result = NULL;
    int done = 0;

    (void)error;
    if (sd_bus_message_read(msg, "sb", &result, &done) < 0 || !result)
        return 0;
    if (strcmp(result, "verify-match") == 0) {
        atomic_store(&ctx->matched, 1);
        return 0;
    }
    if (strcmp(result, "verify-disconnected") == 0 ||
        strcmp(result, "verify-unknown-error") == 0) {
        ctx->verify_dead = 1;
        return 0;
    }
    if (done && strcmp(result, "verify-no-match") == 0)
        ctx->need_restart = 1;
    return 0;
}

static int start_verify(struct auth_ctx *ctx)
{
    sd_bus_message *reply = NULL;
    int r = bus_call(ctx->bus, ctx->device, "VerifyStart", &reply, "s", "any");
    sd_bus_message_unref(reply);
    if (r >= 0)
        ctx->verify_started = 1;
    return r;
}

static void release_device(struct auth_ctx *ctx)
{
    sd_bus_message *reply = NULL;

    if (!ctx->bus || !ctx->device)
        return;
    if (ctx->verify_started) {
        bus_call(ctx->bus, ctx->device, "VerifyStop", &reply, "");
        sd_bus_message_unref(reply);
        reply = NULL;
        ctx->verify_started = 0;
    }
    if (ctx->claimed) {
        bus_call(ctx->bus, ctx->device, "Release", &reply, "");
        sd_bus_message_unref(reply);
        ctx->claimed = 0;
    }
}

static int finger_count(sd_bus *bus, const char *device, const char *user)
{
    sd_bus_error error = SD_BUS_ERROR_NULL;
    sd_bus_message *reply = NULL;
    int r, count = 0;

    r = sd_bus_call_method(bus, "net.reactivated.Fprint", device,
                           "net.reactivated.Fprint.Device",
                           "ListEnrolledFingers", &error, &reply, "s", user);
    if (r < 0) {
        sd_bus_error_free(&error);
        return 0;
    }
    if (sd_bus_message_enter_container(reply, 'a', "s") >= 0) {
        const char *finger = NULL;
        while (sd_bus_message_read(reply, "s", &finger) > 0)
            count++;
    }
    sd_bus_message_unref(reply);
    return count;
}

static int open_reader(struct auth_ctx *ctx)
{
    sd_bus_error error = SD_BUS_ERROR_NULL;
    sd_bus_message *reply = NULL;
    const char *path = NULL;
    char match[512];
    int r;

    r = sd_bus_open_system(&ctx->bus);
    if (r < 0)
        return r;

    r = sd_bus_call_method(ctx->bus, "net.reactivated.Fprint",
                           "/net/reactivated/Fprint/Manager",
                           "net.reactivated.Fprint.Manager", "GetDevices",
                           &error, &reply, "");
    sd_bus_error_free(&error);
    if (r < 0)
        return r;
    if (sd_bus_message_enter_container(reply, 'a', "o") < 0 ||
        sd_bus_message_read(reply, "o", &path) < 0 || !path) {
        sd_bus_message_unref(reply);
        return -ENODEV;
    }
    ctx->device = strdup(path);
    sd_bus_message_unref(reply);
    if (!ctx->device)
        return -ENOMEM;
    if (finger_count(ctx->bus, ctx->device, ctx->user) < 1)
        return -ENOENT;

    snprintf(match, sizeof match,
             "type='signal',sender='net.reactivated.Fprint',path='%s',"
             "interface='net.reactivated.Fprint.Device',member='VerifyStatus'",
             ctx->device);
    r = sd_bus_add_match(ctx->bus, NULL, match, on_verify, ctx);
    if (r < 0)
        return r;

    reply = NULL;
    r = bus_call(ctx->bus, ctx->device, "Claim", &reply, "s", ctx->user);
    sd_bus_message_unref(reply);
    if (r < 0)
        return r;
    ctx->claimed = 1;
    return start_verify(ctx);
}

static int shell_submit(const struct auth_ctx *ctx)
{
    int pipes[2];
    pid_t pid;
    char env_xdg[80], env_bus[128], buf[64];
    char *argv[] = {
        "busctl", "--timeout=1", "call",
        "org.zorin.Fpchoice", "/org/zorin/Fpchoice",
        "org.zorin.Fpchoice", "Submit", NULL
    };
    char *envp[] = { "PATH=/usr/bin:/bin", env_xdg, env_bus, NULL };
    ssize_t n = 0;
    int status = 0;

    snprintf(env_xdg, sizeof env_xdg, "XDG_RUNTIME_DIR=/run/user/%u", ctx->uid);
    snprintf(env_bus, sizeof env_bus, "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%u/bus",
             ctx->uid);
    if (pipe(pipes) != 0)
        return -1;
    pid = fork();
    if (pid < 0) {
        close(pipes[0]);
        close(pipes[1]);
        return -1;
    }
    if (pid == 0) {
        close(pipes[0]);
        if (dup2(pipes[1], STDOUT_FILENO) < 0)
            _exit(127);
        if (dup2(pipes[1], STDERR_FILENO) < 0)
            _exit(127);
        close(pipes[1]);
        if (setgroups(0, NULL) != 0 || setgid(ctx->gid) != 0 || setuid(ctx->uid) != 0)
            _exit(127);
        execve("/usr/bin/busctl", argv, envp);
        _exit(127);
    }
    close(pipes[1]);
    while (n < (ssize_t)sizeof buf - 1) {
        ssize_t got = read(pipes[0], buf + n, sizeof buf - 1 - (size_t)n);
        if (got <= 0)
            break;
        n += got;
    }
    buf[n] = '\0';
    close(pipes[0]);
    waitpid(pid, &status, 0);
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0)
        return -1;
    if (strstr(buf, "true"))
        return 1;
    if (strstr(buf, "false"))
        return 0;
    return -1;
}

static int emit_key(int fd, int code)
{
    struct input_event ev = {0};

    ev.type = EV_KEY;
    ev.code = code;
    ev.value = 1;
    if (write(fd, &ev, sizeof ev) != (ssize_t)sizeof ev)
        return -1;
    ev.type = EV_SYN;
    ev.code = SYN_REPORT;
    ev.value = 0;
    if (write(fd, &ev, sizeof ev) != (ssize_t)sizeof ev)
        return -1;
    usleep(20000);
    ev.type = EV_KEY;
    ev.code = code;
    ev.value = 0;
    if (write(fd, &ev, sizeof ev) != (ssize_t)sizeof ev)
        return -1;
    ev.type = EV_SYN;
    ev.code = SYN_REPORT;
    if (write(fd, &ev, sizeof ev) != (ssize_t)sizeof ev)
        return -1;
    return 0;
}

/* Last resort when the shell helper is not loaded. The dialog is modal. */
static void type_confirm(void)
{
    int fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
    struct uinput_setup setup = {0};

    if (fd < 0)
        return;
    ioctl(fd, UI_SET_EVBIT, EV_KEY);
    ioctl(fd, UI_SET_KEYBIT, KEY_X);
    ioctl(fd, UI_SET_KEYBIT, KEY_ENTER);
    setup.id.bustype = BUS_USB;
    setup.id.vendor = 0x1209;
    setup.id.product = 0x0001;
    snprintf(setup.name, sizeof setup.name, "Fingerprint confirm");
    if (ioctl(fd, UI_DEV_SETUP, &setup) < 0 || ioctl(fd, UI_DEV_CREATE) < 0) {
        close(fd);
        return;
    }
    usleep(700000);
    emit_key(fd, KEY_X);
    usleep(30000);
    emit_key(fd, KEY_ENTER);
    usleep(100000);
    ioctl(fd, UI_DEV_DESTROY);
    close(fd);
}

static void finish_match(struct auth_ctx *ctx)
{
    int i;

    for (i = 0; i < 100 && !atomic_load(&ctx->stop); i++) {
        if (atomic_load(&ctx->prompt_active))
            break;
        usleep(10000);
    }
    if (atomic_load(&ctx->stop) || !atomic_load(&ctx->prompt_active))
        return;

    for (i = 0; i < 20 && atomic_load(&ctx->prompt_active) &&
         !atomic_load(&ctx->stop); i++) {
        int submitted = shell_submit(ctx);
        if (submitted < 0)
            break;
        if (submitted == 1) {
            int j;
            for (j = 0; j < 25 && atomic_load(&ctx->prompt_active); j++)
                usleep(20000);
            if (!atomic_load(&ctx->prompt_active))
                return;
            break;
        }
        usleep(50000);
    }
    if (atomic_load(&ctx->prompt_active) && !atomic_load(&ctx->stop))
        type_confirm();
}

static void *verify_thread(void *arg)
{
    struct auth_ctx *ctx = arg;
    int r = open_reader(ctx);

    if (r < 0) {
        syslog(LOG_AUTH | LOG_NOTICE,
               "fingerprint not used for this admin prompt (%s)", strerror(-r));
        release_device(ctx);
        return NULL;
    }

    while (!atomic_load(&ctx->stop) && !atomic_load(&ctx->matched) &&
           !ctx->verify_dead) {
        if (ctx->need_restart) {
            ctx->need_restart = 0;
            if (++ctx->tries >= 8)
                break;
            start_verify(ctx);
        }
        r = sd_bus_process(ctx->bus, NULL);
        if (r < 0)
            break;
        if (r > 0)
            continue;
        sd_bus_wait(ctx->bus, 200000);
    }

    release_device(ctx);
    if (atomic_load(&ctx->matched))
        finish_match(ctx);
    return NULL;
}

static void stop_thread(struct auth_ctx *ctx)
{
    atomic_store(&ctx->stop, 1);
    if (ctx->have_thread) {
        pthread_join(ctx->thread, NULL);
        ctx->have_thread = 0;
    }
    sd_bus_unref(ctx->bus);
    ctx->bus = NULL;
    free(ctx->device);
    ctx->device = NULL;
}

PAM_EXTERN int pam_sm_authenticate(pam_handle_t *pamh, int flags, int argc,
                                   const char **argv)
{
    const char *user = NULL;
    struct passwd *pw;
    struct auth_ctx ctx;
    char *response = NULL;
    int r;

    (void)flags;
    (void)argc;
    (void)argv;
    memset(&ctx, 0, sizeof ctx);
    ctx.pamh = pamh;

    if (pam_get_user(pamh, &user, NULL) != PAM_SUCCESS || !user || !user[0])
        return PAM_IGNORE;
    pw = getpwnam(user);
    if (!pw)
        return PAM_IGNORE;
    ctx.user = strdup(user);
    ctx.uid = pw->pw_uid;
    ctx.gid = pw->pw_gid;
    if (!ctx.user)
        return PAM_IGNORE;

    if (pthread_create(&ctx.thread, NULL, verify_thread, &ctx) != 0) {
        free(ctx.user);
        return PAM_IGNORE;
    }
    ctx.have_thread = 1;

    atomic_store(&ctx.prompt_active, 1);
    pam_info(pamh, "Place your finger or enter your password");
    r = pam_prompt(pamh, PAM_PROMPT_ECHO_OFF, &response, "%s", "Password:");
    atomic_store(&ctx.prompt_active, 0);

    if (r == PAM_SUCCESS && response && strcmp(response, MATCH_MARK) == 0) {
        int i;
        for (i = 0; i < 30 && !atomic_load(&ctx.matched); i++)
            usleep(10000);
    }

    if (r == PAM_SUCCESS && atomic_load(&ctx.matched)) {
        wipe(response);
        stop_thread(&ctx);
        free(ctx.user);
        pam_syslog(pamh, LOG_NOTICE, "fingerprint accepted");
        return PAM_SUCCESS;
    }

    stop_thread(&ctx);
    if (r != PAM_SUCCESS) {
        wipe(response);
        free(ctx.user);
        return PAM_CONV_ERR;
    }
    if (!response || !response[0] || strcmp(response, MATCH_MARK) == 0) {
        wipe(response);
        free(ctx.user);
        return PAM_AUTH_ERR;
    }

    r = pam_set_item(pamh, PAM_AUTHTOK, response);
    wipe(response);
    free(ctx.user);
    return r == PAM_SUCCESS ? PAM_IGNORE : PAM_AUTH_ERR;
}

PAM_EXTERN int pam_sm_setcred(pam_handle_t *pamh, int flags, int argc,
                              const char **argv)
{
    (void)pamh;
    (void)flags;
    (void)argc;
    (void)argv;
    return PAM_SUCCESS;
}
