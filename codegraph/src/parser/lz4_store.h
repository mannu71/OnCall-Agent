#ifndef CG_LZ4_STORE_H
#define CG_LZ4_STORE_H

// LZ4 HC compression (level 9).
int cg_lz4_compress_hc(const char *src, int srcLen, char *dst, int dstCap);

// LZ4 decompression.
int cg_lz4_decompress(const char *src, int srcLen, char *dst, int originalLen);

// Maximum compressed size bound.
int cg_lz4_bound(int inputSize);

#endif // CG_LZ4_STORE_H
