#include <ctype.h>
#include <errno.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>

#include "fuzz.h"
#include "schema.h"

#define DEFAULT_MEMORY_BITS 32U
#define MAX_MEMORY_BITS 64U
#define MAX_NESTING 64U

struct Parser {
    const char *cursor;
    unsigned int nesting;
};

static void skip_space(struct Parser *parser)
{
    while (isspace((unsigned char)*parser->cursor)) {
        parser->cursor++;
    }
}

static bool consume(struct Parser *parser, char expected)
{
    skip_space(parser);
    if (*parser->cursor != expected) {
        return false;
    }
    parser->cursor++;
    return true;
}

static bool parse_number(struct Parser *parser, uint64_t *value)
{
    char *end;
    int base = 10;

    skip_space(parser);
    if (!isdigit((unsigned char)*parser->cursor)) {
        return false;
    }
    if (parser->cursor[0] == '0' &&
        (parser->cursor[1] == 'x' || parser->cursor[1] == 'X')) {
        if (!isxdigit((unsigned char)parser->cursor[2])) {
            return false;
        }
        base = 16;
    }
    errno = 0;
    *value = strtoull(parser->cursor, &end, base);
    if (errno == ERANGE || end == parser->cursor) {
        return false;
    }
    parser->cursor = end;
    return true;
}

static bool location_value(const struct Location *location, uint64_t *value)
{
    if (location->type == Register) {
        *value = fuzz_get_register(location->val.reg);
        return true;
    }
    if (location->type == Memory) {
        *value = location->val.address;
        return true;
    }
    return false;
}

static bool read_unsigned(uint64_t address, uint64_t bits, uint64_t *value)
{
    uint8_t bytes[MAX_MEMORY_BITS / 8U] = {0};
    size_t bytes_count;
    size_t i;

    if (bits == 0 || bits > MAX_MEMORY_BITS) {
        return false;
    }
    bytes_count = (size_t)((bits + 7U) / 8U);
    if (fuzz_read_memory(address, bytes, (int)bytes_count) != 0) {
        return false;
    }
    *value = 0;
    for (i = 0; i < bytes_count; i++) {
        *value |= (uint64_t)bytes[i] << (i * 8U);
    }
    if (bits < MAX_MEMORY_BITS) {
        *value &= UINT64_MAX >> (MAX_MEMORY_BITS - bits);
    }
    return true;
}

static bool parse_expression(struct Parser *parser, struct Location *result);

static bool parse_nested(struct Parser *parser, struct Location *result)
{
    bool ok;

    if (parser->nesting == MAX_NESTING) {
        return false;
    }
    parser->nesting++;
    ok = parse_expression(parser, result);
    parser->nesting--;
    return ok;
}

static bool parse_register_value(struct Parser *parser, struct Location *result)
{
    char *end;
    unsigned long long reg;

    skip_space(parser);
    if (*parser->cursor != 'r' || !isdigit((unsigned char)parser->cursor[1])) {
        return false;
    }
    errno = 0;
    reg = strtoull(parser->cursor + 1, &end, 10);
    if (errno == ERANGE || reg > INT_MAX) {
        return false;
    }
    parser->cursor = end;
    result->type = Memory;
    result->val.address = fuzz_get_register((int)reg);
    return true;
}

static bool parse_primary(struct Parser *parser, struct Location *result)
{
    struct Location address;
    uint64_t bits;
    uint64_t number;

    skip_space(parser);
    if (*parser->cursor == 'r') {
        return parse_register_value(parser, result);
    }
    if (*parser->cursor == 'u') {
        parser->cursor++;
        if (!parse_number(parser, &bits) || !consume(parser, '[') ||
            !parse_nested(parser, &address) || !consume(parser, ']') ||
            !location_value(&address, &number) ||
            !read_unsigned(number, bits, &number)) {
            return false;
        }
        result->type = Memory;
        result->val.address = number;
        return true;
    }
    if (isdigit((unsigned char)*parser->cursor)) {
        if (!parse_number(parser, &number)) {
            return false;
        }
        result->type = Memory;
        result->val.address = number;
        return true;
    }
    if (consume(parser, '[')) {
        if (!parse_nested(parser, &address) || !consume(parser, ']') ||
            !location_value(&address, &number) ||
            !read_unsigned(number, DEFAULT_MEMORY_BITS, &number)) {
            return false;
        }
        result->type = Memory;
        result->val.address = number;
        return true;
    }
    if (consume(parser, '(')) {
        return parse_nested(parser, result) && consume(parser, ')');
    }
    return false;
}

static bool parse_term(struct Parser *parser, struct Location *result)
{
    struct Location rhs;
    uint64_t left;
    uint64_t right;
    char op;

    if (!parse_primary(parser, result)) {
        return false;
    }
    for (;;) {
        skip_space(parser);
        op = *parser->cursor;
        if (op != '*' && op != '/') {
            return true;
        }
        parser->cursor++;
        if (!parse_primary(parser, &rhs) || !location_value(result, &left) ||
            !location_value(&rhs, &right) || (op == '/' && right == 0)) {
            return false;
        }
        result->type = Memory;
        result->val.address = op == '*' ? left * right : left / right;
    }
}

static bool parse_expression(struct Parser *parser, struct Location *result)
{
    struct Location rhs;
    uint64_t left;
    uint64_t right;
    char op;

    if (!parse_term(parser, result)) {
        return false;
    }
    for (;;) {
        skip_space(parser);
        op = *parser->cursor;
        if (op != '+' && op != '-') {
            return true;
        }
        parser->cursor++;
        if (!parse_term(parser, &rhs) || !location_value(result, &left) ||
            !location_value(&rhs, &right)) {
            return false;
        }
        result->type = Memory;
        result->val.address = op == '+' ? left + right : left - right;
    }
}

bool generic_parse_expression(const char *expression, struct Location *result)
{
    struct Parser parser = { .cursor = expression, .nesting = 0 };
    uint64_t reg;

    if (expression == NULL || result == NULL) {
        return false;
    }
    skip_space(&parser);
    if (strncmp(parser.cursor, "reg", 3) == 0) {
        parser.cursor += 3;
        if (!consume(&parser, '(') || !parse_number(&parser, &reg) ||
            reg > INT_MAX || !consume(&parser, ')')) {
            return false;
        }
        result->type = Register;
        result->val.reg = (int)reg;
    } else if (!parse_expression(&parser, result)) {
        return false;
    }
    skip_space(&parser);
    return *parser.cursor == '\0';
}
