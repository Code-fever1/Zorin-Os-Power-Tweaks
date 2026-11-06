CFLAGS := -shared -fPIC -O2 -Wall -Wextra -Werror -fno-plt
LDFLAGS := -Wl,-z,relro,-z,now -lsystemd -lpam -lpthread

pam_fpchoice.so: pam_fpchoice.c
	gcc $(CFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f pam_fpchoice.so
