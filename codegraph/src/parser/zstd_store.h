#ifndef CG_ZSTD_STORE_H
#define CG_ZSTD_STORE_H

#include <stddef.h>

// Zstd compression at specified level (1=fast .. 22=best).
// Returns compressed size on success, 0 on error.
int cg_zstd_compress(const char *src, int srcLen, char *dst, int dstCap, int level);

// Zstd decompression.
// Returns decompressed size on success, 0 on error.
int cg_zstd_decompress(const char *src, int srcLen, char *dst, int dstCap);

// Maximum compressed size bound for given input size.
size_t cg_zstd_compress_bound(int inputSize);

#endif // CG_ZSTD_STORE_H
