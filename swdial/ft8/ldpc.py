"""LDPC(174,91) encoder and a batched log-domain belief-propagation decoder.

LLR convention used throughout: llr > 0 means the bit is more likely a 1.
"""
import numpy as np

from .constants import LDPC_GENERATOR_HEX, LDPC_MN

N, K, M = 174, 91, 83


def _generator() -> np.ndarray:
    g = np.zeros((M, K), np.uint8)
    for i, h in enumerate(LDPC_GENERATOR_HEX):
        bits = bin(int(h, 16))[2:].zfill(92)[:K]
        g[i] = [int(c) for c in bits]
    return g


G = _generator()
H = np.zeros((M, N), np.uint8)
for _v, _checks in enumerate(LDPC_MN):
    for _c in _checks:
        H[_c - 1, _v] = 1

# Edge lists: check-major, padded to the max row weight.
_ROW_W = H.sum(1)
W = int(_ROW_W.max())
CHECK_VARS = np.zeros((M, W), np.int64)      # var index for each check slot
CHECK_MASK = np.zeros((M, W), bool)
for _c in range(M):
    vs = np.nonzero(H[_c])[0]
    CHECK_VARS[_c, :len(vs)] = vs
    CHECK_MASK[_c, :len(vs)] = True
_EV = CHECK_VARS[CHECK_MASK]                  # var per edge (flattened, check-major)
_EC = np.nonzero(CHECK_MASK)[0]               # check per edge
N_EDGES = _EV.size
_EDGE_TO_VAR = np.zeros((N_EDGES, N))
_EDGE_TO_VAR[np.arange(N_EDGES), _EV] = 1.0


def encode(a91) -> np.ndarray:
    a91 = np.asarray(a91, np.uint8)
    parity = (G.astype(np.int64) @ a91) % 2
    return np.concatenate([a91, parity.astype(np.uint8)])


def syndrome_ok(cw) -> bool:
    return not np.any((H.astype(np.int64) @ np.asarray(cw, np.int64)) % 2)


def bp_decode(llr, max_iter: int = 30):
    """Decode a batch of LLR vectors, shape (B, 174).

    Returns (bits (B,174) uint8, n_errors (B,) int) where n_errors is the
    number of unsatisfied parity checks at exit (0 = valid codeword).
    """
    # internally use L = log(P0/P1) so the plain tanh rule applies
    llr = -np.atleast_2d(np.asarray(llr, np.float64))
    B = llr.shape[0]
    v2c = llr[:, _EV].copy()                 # (B, E)
    c2v = np.zeros_like(v2c)
    best_bits = (llr < 0).astype(np.uint8)
    best_err = np.full(B, M + 1)
    done = np.zeros(B, bool)
    out_bits = best_bits.copy()
    for _ in range(max_iter):
        # check-node update (tanh rule) on a padded (B, M, W) view
        t = np.ones((B, M, W))
        t[:, CHECK_MASK] = np.tanh(np.clip(v2c, -30, 30) / 2.0)
        t = np.where(np.abs(t) < 1e-12, 1e-12, t)
        prod = np.prod(t, axis=2, keepdims=True)
        ext = np.clip(prod / t, -0.999999, 0.999999)
        c2v = 2.0 * np.arctanh(ext[:, CHECK_MASK])
        # variable-node update
        tot = llr + c2v @ _EDGE_TO_VAR
        bits = (tot < 0).astype(np.uint8)
        nerr = ((bits[:, CHECK_VARS] * CHECK_MASK).sum(2) % 2).sum(1)
        newly = (nerr == 0) & ~done
        out_bits[newly] = bits[newly]
        done |= nerr == 0
        better = (nerr < best_err) & ~done
        best_err[better] = nerr[better]
        best_bits[better] = bits[better]
        if done.all():
            break
        v2c = tot[:, _EV] - c2v
    out_bits[~done] = best_bits[~done]
    nerr_final = np.where(done, 0, best_err)
    return out_bits, nerr_final
