"""numba when available (fast), plain Python otherwise (same results, much slower)."""
try:                                    # pragma: no cover - depends on the environment
    from numba import njit
    HAVE_NUMBA = True
except ImportError:                     # pragma: no cover
    HAVE_NUMBA = False

    def njit(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda f: f
