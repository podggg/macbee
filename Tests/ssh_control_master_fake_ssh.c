// Test-only fake ssh / ssh-add binary for A3/A5/A6 ControlMaster acceptance tests.
// Uses local Unix domain sockets and synthetic SHA-256 passphrase checks only.
#include <CommonCrypto/CommonDigest.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

static volatile sig_atomic_t g_stop = 0;
static void on_sig(int sig) { (void)sig; g_stop = 1; }

static void append_argv_log(const char *path, int argc, char **argv) {
    if (!path || !*path) return;
    FILE *f = fopen(path, "a");
    if (!f) return;
    for (int i = 0; i < argc; i++) {
        fprintf(f, "%s%s", i ? " " : "", argv[i]);
    }
    fputc('\n', f);
    fclose(f);
}

static int expand_control_path(const char *tmpl, const char *target, char *out, size_t out_sz) {
    const char *pct = strstr(tmpl, "%C");
    if (!pct) {
        if (strlen(tmpl) >= out_sz) return -1;
        strlcpy(out, tmpl, out_sz);
        return 0;
    }
    unsigned char h[CC_SHA1_DIGEST_LENGTH];
    CC_SHA1(target, (CC_LONG)strlen(target), h);
    char hex[41] = {0};
    for (int i = 0; i < CC_SHA1_DIGEST_LENGTH; i++) {
        snprintf(hex + 2 * i, 3, "%02x", h[i]);
    }
    size_t prefix = (size_t)(pct - tmpl);
    size_t suffix = strlen(pct + 2);
    if (prefix + 40 + suffix >= out_sz) return -1;
    memcpy(out, tmpl, prefix);
    memcpy(out + prefix, hex, 40);
    memcpy(out + prefix + 40, pct + 2, suffix + 1);
    return 0;
}

static int askpass_check(const char *prompt, const char *prompt_hint, const char *expected_sha256) {
    const char *helper = getenv("SSH_ASKPASS");
    if (!helper || !*helper) return 1;
    int f[2];
    if (pipe(f) != 0) return 1;
    pid_t pid = fork();
    if (pid < 0) { close(f[0]); close(f[1]); return 1; }
    if (pid == 0) {
        close(f[0]);
        dup2(f[1], STDOUT_FILENO);
        close(f[1]);
        if (prompt_hint && *prompt_hint) setenv("SSH_ASKPASS_PROMPT", prompt_hint, 1);
        else unsetenv("SSH_ASKPASS_PROMPT");
        execl(helper, helper, prompt, (char *)0);
        _exit(127);
    }
    close(f[1]);
    unsigned char b[1024] = {0};
    size_t used = 0;
    ssize_t got;
    while (used < sizeof(b) && (got = read(f[0], b + used, sizeof(b) - used)) > 0) used += (size_t)got;
    close(f[0]);
    int status = 0;
    while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}
    if (!used || b[used - 1] != '\n' || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        memset(b, 0, sizeof(b));
        return 1;
    }
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    char hex[65] = {0};
    CC_SHA256(b, (CC_LONG)(used - 1), digest);
    memset(b, 0, sizeof(b));
    for (unsigned i = 0; i < sizeof(digest); i++) snprintf(hex + 2 * i, 3, "%02x", digest[i]);
    return strcmp(hex, expected_sha256) == 0 ? 0 : 2;
}

static int send_ctl_cmd(const char *sock_path, const char *cmd) {
    struct sockaddr_un a;
    memset(&a, 0, sizeof(a));
    if (strlen(sock_path) >= sizeof(a.sun_path)) return 255;
    a.sun_family = AF_UNIX;
    strlcpy(a.sun_path, sock_path, sizeof(a.sun_path));
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return 255;
    if (connect(fd, (struct sockaddr *)&a, sizeof(a)) != 0) { close(fd); return 255; }
    size_t len = strlen(cmd);
    if (write(fd, cmd, len) != (ssize_t)len) { close(fd); return 255; }
    char resp[16] = {0};
    ssize_t n = read(fd, resp, sizeof(resp) - 1);
    close(fd);
    if (n <= 0) return 255;
    return strncmp(resp, "OK", 2) == 0 ? 0 : 255;
}

int main(int argc, char **argv) {
    const char *prog = strrchr(argv[0], '/');
    prog = prog ? prog + 1 : argv[0];

    if (strcmp(prog, "fake-ssh-add") == 0) {
        append_argv_log(getenv("FIXTURE_SSH_ADD_LOG"), argc, argv);
        const char *id_path = argc >= 2 ? argv[argc - 1] : NULL;
        const char *kc = getenv("FIXTURE_KEYCHAIN_SHA256");
        const char *expected = kc ? kc : getenv("FIXTURE_EXPECTED_SHA256");
        if (!id_path || !expected) return 1;
        char prompt[700];
        snprintf(prompt, sizeof(prompt), "Enter passphrase for %s: ", id_path);
        return askpass_check(prompt, NULL, expected) == 0 ? 0 : 1;
    }

    const char *ctl_cmd = NULL;
    const char *ctl_path = NULL;
    const char *target = argc >= 2 ? argv[argc - 1] : "";
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-O") == 0 && i + 1 < argc) ctl_cmd = argv[++i];
        else if (strcmp(argv[i], "-S") == 0 && i + 1 < argc) ctl_path = argv[++i];
        else if (strncmp(argv[i], "ControlPath=", 12) == 0) ctl_path = argv[i] + 12;
    }

    if (ctl_cmd) {
        append_argv_log(getenv("FIXTURE_CTL_LOG"), argc, argv);
        if (!ctl_path || !*target) return 255;
        char sock[256];
        if (expand_control_path(ctl_path, target, sock, sizeof(sock)) != 0) return 255;
        if (strcmp(ctl_cmd, "check") == 0) return send_ctl_cmd(sock, "PING\n");
        if (strcmp(ctl_cmd, "exit") == 0) return send_ctl_cmd(sock, "EXIT\n");
        return 255;
    }

    append_argv_log(getenv("FIXTURE_ARGV_LOG"), argc, argv);

    const char *raw_mode = getenv("FIXTURE_MODE");
    const char *mode = raw_mode ? raw_mode : "passphrase";
    if (strcmp(mode, "changed_key") == 0) {
        fprintf(stderr,
                "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
                "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
                "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
                "Host key verification failed.\n");
        return 255;
    }

    if (strcmp(mode, "trust_then_passphrase") == 0) {
        const char *fp = getenv("FIXTURE_FP_SHA256");
        if (!fp) return 255;
        const char *trust_prompt =
            "The authenticity of host 'fixture.local (127.0.0.1)' can't be established.\n"
            "ED25519 key fingerprint is SHA256:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB.\n"
            "Are you sure you want to continue connecting (yes/no/[fingerprint])? ";
        if (askpass_check(trust_prompt, "confirm", fp) != 0) {
            fprintf(stderr, "Host key verification failed.\n");
            return 255;
        }
    }

    if (strcmp(mode, "passphrase") == 0 || strcmp(mode, "trust_then_passphrase") == 0 || strcmp(mode, "cancel") == 0) {
        const char *raw_key = getenv("FIXTURE_KEY_PATH");
        const char *key_path = raw_key ? raw_key : "/tmp/fixture_id_ed25519";
        const char *raw_exp = getenv("FIXTURE_EXPECTED_SHA256");
        const char *expected = raw_exp ? raw_exp : "";
        char prompt[700];
        snprintf(prompt, sizeof(prompt), "Enter passphrase for key '%s': ", key_path);
        int rc = askpass_check(prompt, NULL, expected);
        if (rc != 0) {
            fprintf(stderr, "Permission denied (publickey).\n");
            return 255;
        }
    }

    if (!ctl_path) return 255;
    char sock[256];
    if (expand_control_path(ctl_path, target, sock, sizeof(sock)) != 0) return 255;

    // Verify private control directory is 0700 and sun_path < 104 before binding.
    char dir[256];
    strlcpy(dir, sock, sizeof(dir));
    char *slash = strrchr(dir, '/');
    if (!slash) return 255;
    *slash = '\0';
    struct stat dst;
    if (lstat(dir, &dst) != 0 || !S_ISDIR(dst.st_mode) || dst.st_uid != getuid() || (dst.st_mode & 0777) != 0700) {
        return 255;
    }

    struct sockaddr_un a;
    memset(&a, 0, sizeof(a));
    if (strlen(sock) >= sizeof(a.sun_path) || strlen(sock) + 17 >= sizeof(a.sun_path)) return 255;
    a.sun_family = AF_UNIX;
    strlcpy(a.sun_path, sock, sizeof(a.sun_path));

    int lfd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (lfd < 0) return 255;
    if (bind(lfd, (struct sockaddr *)&a, sizeof(a)) != 0 || chmod(sock, 0600) != 0 || listen(lfd, 8) != 0) {
        close(lfd);
        return 255;
    }

    const char *pid_file = getenv("FIXTURE_PID_FILE");
    if (pid_file && *pid_file) {
        FILE *pf = fopen(pid_file, "a");
        if (pf) { fprintf(pf, "%d\n", (int)getpid()); fclose(pf); }
    }

    signal(SIGTERM, on_sig);
    signal(SIGINT, on_sig);
    while (!g_stop) {
        struct pollfd p = { .fd = lfd, .events = POLLIN };
        int ready = poll(&p, 1, 100);
        if (ready <= 0) continue;
        int cfd = accept(lfd, NULL, NULL);
        if (cfd < 0) continue;
        char req[16] = {0};
        ssize_t n = read(cfd, req, sizeof(req) - 1);
        if (n > 0 && strncmp(req, "EXIT", 4) == 0) {
            (void)write(cfd, "OK\n", 3);
            close(cfd);
            break;
        }
        (void)write(cfd, "OK\n", 3);
        close(cfd);
    }

    close(lfd);
    unlink(sock);
    return 0;
}
