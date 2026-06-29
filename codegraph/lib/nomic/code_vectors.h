#ifndef CG_NOMIC_VECTORS_H
#define CG_NOMIC_VECTORS_H

#include <stdint.h>

#define PRETRAINED_TOKEN_COUNT 40856
#define PRETRAINED_DIM 768

/* Raw vector blob: first 8 bytes = [int32 count][int32 dim],
 * then count x dim int8 values (unit-normalized, x127 scaled). */
extern const unsigned char PRETRAINED_VECTOR_BLOB[];
extern const unsigned int PRETRAINED_VECTOR_BLOB_LEN;

/* Access the int8 vector for token index i. Zero-copy pointer into blob. */
static inline const int8_t *pretrained_vec_at(int i) {
    return (const int8_t *)(PRETRAINED_VECTOR_BLOB + 8 + (size_t)i * PRETRAINED_DIM);
}

/* Token strings (separate header to keep this file clean). */
#include "code_tokens.h"

#endif /* CG_NOMIC_VECTORS_H */
