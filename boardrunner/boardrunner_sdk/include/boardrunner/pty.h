#pragma once
#include <stdint.h>
#include <device.h>

int api_pty_fd_gen_impl(const char* dev_name);
void api_pty_write_req(int fd, uint8_t value);
int api_pty_read_nonblock(int fd, uint8_t *buff);

/* Call as api_pty_fd_gen() for the default device, or
 * api_pty_fd_gen("usart1") to pick a specific one. */
#define _PTY_FIRST_ARG(_0, arg, ...) arg
#define api_pty_fd_gen(...) \
    api_pty_fd_gen_impl(_PTY_FIRST_ARG(_, ##__VA_ARGS__, NULL))
