/* TANKSIM — нативное ядро боя.
 *
 * Это построчный перенос engine/world.py, engine/tank.py, engine/projectile.py,
 * engine/visibility.py, engine/geometry.py и engine/grid.py на C. Задача —
 * побитово тот же результат: те же числа, тот же порядок операций, тот же
 * порядок обхода. Поэтому здесь:
 *
 *   · все тригонометрические вызовы идут в ту же libm, что и у math.*;
 *   · hypot — это НЕ libm hypot, а копия vector_norm из CPython
 *     (Modules/mathmodule.c): python'овский hypot считает с компенсацией,
 *     и его ответ отличается от системного в последних битах;
 *   · round(x, n) — не rint(x*10**n)/10**n, а точное десятичное округление
 *     половина-к-чётному на 128-битных целых, как float.__round__;
 *   · MT19937 и инициализация по массиву взяты из CPython random.Random —
 *     иначе разброс ствола пойдёт по другим числам и бой разойдётся.
 *
 * Проверка равенства — tools/parity.py (сверяет состояние каждый тик).
 *
 * Сборка: python -m engine.native.build  (gcc/cc/clang, без Python.h)
 */

#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#if !defined(__SIZEOF_INT128__)
#error "нужен компилятор с __int128 (gcc/clang)"
#endif

typedef __int128 i128;
typedef unsigned __int128 u128;

#define NT_MAX_BULLETS 128
/*: Полей в буфере наблюдения (см. sim_obs_layout в engine/native/api.py). */
#define NT_OBS_N 48
/*: Полей в дампе состояния (для сверки с Python-движком). */
#define NT_STATE_N 1800

/* ===================================================================== */
/* примитивы, совпадающие с CPython                                      */
/* ===================================================================== */

static inline int nt_isnan(double x) { return x != x; }
static inline int nt_isinf(double x) { return isinf(x) != 0; }

/* math.hypot(x, y) — vector_norm() из Modules/mathmodule.c (CPython 3.11). */
static double nt_vector_norm(int n, double *vec, double max, int found_nan)
{
    const double T27 = 134217729.0;     /* ldexp(1.0, 27) + 1.0 */
    double x, scale, oldcsum, csum = 1.0, frac1 = 0.0, frac2 = 0.0, frac3 = 0.0;
    double t, hi, lo, h;
    int max_e, i;

    if (nt_isinf(max)) return max;
    if (found_nan) return (double)NAN;
    if (max == 0.0 || n <= 1) return max;
    frexp(max, &max_e);
    if (max_e >= -1023) {
        scale = ldexp(1.0, -max_e);
        for (i = 0; i < n; i++) {
            x = vec[i] * scale;
            t = x * T27;
            hi = t - (t - x);
            lo = x - hi;
            x = hi * hi;
            oldcsum = csum; csum += x; frac1 += (oldcsum - csum) + x;
            x = 2.0 * hi * lo;
            oldcsum = csum; csum += x; frac2 += (oldcsum - csum) + x;
            frac3 += lo * lo;
        }
        h = sqrt(csum - 1.0 + (frac1 + frac2 + frac3));
        x = h;
        t = x * T27;
        hi = t - (t - x);
        lo = x - hi;
        x = -hi * hi;
        oldcsum = csum; csum += x; frac1 += (oldcsum - csum) + x;
        x = -2.0 * hi * lo;
        oldcsum = csum; csum += x; frac2 += (oldcsum - csum) + x;
        x = -lo * lo;
        oldcsum = csum; csum += x; frac3 += (oldcsum - csum) + x;
        x = csum - 1.0 + (frac1 + frac2 + frac3);
        return (h + x / (2.0 * h)) / scale;
    }
    for (i = 0; i < n; i++) {
        x = vec[i] / max;
        x = x * x;
        oldcsum = csum; csum += x; frac1 += (oldcsum - csum) + x;
    }
    return max * sqrt(csum - 1.0 + frac1);
}

/*: math.hypot(x, y) побитово. */
static double nt_hypot(double x, double y)
{
    double vec[2];
    double max = 0.0;
    int found_nan;
    x = fabs(x);
    y = fabs(y);
    vec[0] = x;
    vec[1] = y;
    found_nan = (x != x) | (y != y);
    if (x > max) max = x;
    if (y > max) max = y;
    return nt_vector_norm(2, vec, max, found_nan);
}

static double nt_pow10(int n)
{
    double p = 1.0;
    while (n-- > 0) p *= 10.0;
    return p;
}

/* round(x, n) как float.__round__: точное десятичное округление,
 * половина — к чётному, затем деление на 10**n (оба операнда точны,
 * поэтому IEEE-деление даёт ближайший double к десятичному числу). */
static double nt_round(double x, int n)
{
    double p10, res, f;
    int e, neg, sh;
    uint64_t m;
    i128 num;
    u128 u, q, rem, half;

    if (x != x || nt_isinf(x)) return x;
    if (x == 0.0) return x;
    neg = x < 0.0;
    if (neg) x = -x;
    if (n < 0) return x;                 /* в движке отрицательных n нет */
    p10 = nt_pow10(n);
    f = frexp(x, &e);                    /* x = f * 2^e, 0.5 <= f < 1 */
    m = (uint64_t)ldexp(f, 53);          /* m в [2^52, 2^53) */
    e -= 53;                             /* x = m * 2^e — точно */
    num = (i128)m * (i128)(uint64_t)(p10 + 0.5);
    if (e >= 0) {
        i128 d = (e > 62) ? num : (num << e);
        res = (double)d / p10;
        return neg ? -res : res;
    }
    sh = -e;
    u = (u128)num;
    if (sh > 100) {
        q = 0;
    } else {
        q = u >> sh;
        rem = u & ((((u128)1) << sh) - 1);
        half = (u128)1 << (sh - 1);
        if (rem > half || (rem == half && (q & 1))) q += 1;
    }
    res = (double)(i128)q / p10;
    return neg ? -res : res;
}

/* ===================================================================== */
/* random.Random (MT19937 + init_by_array)                               */
/* ===================================================================== */

typedef struct {
    uint32_t mt[624];
    int mti;
} nt_rng;

static void nt_rng_genrand(nt_rng *r, uint32_t s)
{
    int i;
    r->mt[0] = s;
    for (i = 1; i < 624; i++)
        r->mt[i] = (uint32_t)(1812433253UL * (r->mt[i - 1] ^ (r->mt[i - 1] >> 30))
                              + (uint32_t)i);
    r->mti = 624;
}

static void nt_rng_by_array(nt_rng *r, const uint32_t *key, int key_length)
{
    int i = 1, j = 0, k;
    nt_rng_genrand(r, 19650218UL);
    k = (624 > key_length ? 624 : key_length);
    for (; k; k--) {
        r->mt[i] = (uint32_t)((r->mt[i] ^ ((r->mt[i - 1] ^ (r->mt[i - 1] >> 30)) * 1664525UL))
                              + key[j] + (uint32_t)j);
        i++; j++;
        if (i >= 624) { r->mt[0] = r->mt[623]; i = 1; }
        if (j >= key_length) j = 0;
    }
    for (k = 623; k; k--) {
        r->mt[i] = (uint32_t)((r->mt[i] ^ ((r->mt[i - 1] ^ (r->mt[i - 1] >> 30)) * 1566083941UL))
                              - (uint32_t)i);
        i++;
        if (i >= 624) { r->mt[0] = r->mt[623]; i = 1; }
    }
    r->mt[0] = 0x80000000UL;
}

/*: random.Random(целое).seed + init_by_array по 32-битным словам модуля. */
static void nt_rng_seed(nt_rng *r, long long seed)
{
    uint64_t n = (uint64_t)(seed < 0 ? -(uint64_t)seed : (uint64_t)seed);
    uint32_t key[8];
    int used = 0;
    if (n == 0) {
        key[used++] = 0;
    }
    while (n && used < 8) {
        key[used++] = (uint32_t)(n & 0xffffffffULL);
        n >>= 32;
    }
    nt_rng_by_array(r, key, used);
}

static uint32_t nt_rng_u32(nt_rng *r)
{
    static const uint32_t mag01[2] = {0x0UL, 0x9908b0dfUL};
    uint32_t y;
    int kk;
    if (r->mti >= 624) {
        for (kk = 0; kk < 624 - 397; kk++) {
            y = (r->mt[kk] & 0x80000000UL) | (r->mt[kk + 1] & 0x7fffffffUL);
            r->mt[kk] = r->mt[kk + 397] ^ (y >> 1) ^ mag01[y & 0x1UL];
        }
        for (; kk < 623; kk++) {
            y = (r->mt[kk] & 0x80000000UL) | (r->mt[kk + 1] & 0x7fffffffUL);
            r->mt[kk] = r->mt[kk + (397 - 624)] ^ (y >> 1) ^ mag01[y & 0x1UL];
        }
        y = (r->mt[623] & 0x80000000UL) | (r->mt[0] & 0x7fffffffUL);
        r->mt[623] = r->mt[396] ^ (y >> 1) ^ mag01[y & 0x1UL];
        r->mti = 0;
    }
    y = r->mt[r->mti++];
    y ^= (y >> 11);
    y ^= (y << 7) & 0x9d2c5680UL;
    y ^= (y << 15) & 0xefc60000UL;
    y ^= (y >> 18);
    return y;
}

/*: random.Random.random() — genrand_res53. */
static double nt_rng_random(nt_rng *r)
{
    uint32_t a = nt_rng_u32(r) >> 5;
    uint32_t b = nt_rng_u32(r) >> 6;
    return (a * 67108864.0 + b) * (1.0 / 9007199254740992.0);
}

/* ===================================================================== */
/* арена (engine/grid.py)                                                */
/* ===================================================================== */

typedef struct {
    int width, height, tile;
    double tile_f;
    double pixel_width, pixel_height;
    double mud_factor;
    int has_mud;

    unsigned char *opaque;      /* width*height */
    unsigned char *block;       /* width*height */
    unsigned char *mud;         /* width*height */
    unsigned char *opaque_pad;  /* (width+4)*(height+4) */

    /* block_cols: CSR по строкам — отсортированные номера непроходимых тайлов */
    int *cols_off;              /* height+1 */
    int *cols;                  /* суммарно */

    /* near_blocks: CSR по тайлам — прямоугольники непроходимых тайлов 3x3 */
    int *near_off;              /* height*width+1 */
    double *near_box;           /* 4 * суммарно */

    double *clearance;          /* width*height, расстояние в тайлах */
} nt_arena;

static inline int nt_opaque_px(const nt_arena *a, double px, double py)
{
    int tx = (int)floor(px / a->tile);
    int ty = (int)floor(py / a->tile);
    if (tx < 0 || ty < 0 || ty >= a->height || tx >= a->width) return 1;
    return a->opaque[ty * a->width + tx] != 0;
}

static inline int nt_blocked_px(const nt_arena *a, double px, double py)
{
    int tx = (int)floor(px / a->tile);
    int ty = (int)floor(py / a->tile);
    if (tx < 0 || ty < 0 || ty >= a->height || tx >= a->width) return 1;
    return a->block[ty * a->width + tx] != 0;
}

static inline double nt_speed_factor(const nt_arena *a, double px, double py)
{
    int tx = (int)floor(px / a->tile);
    int ty = (int)floor(py / a->tile);
    if (tx >= 0 && tx < a->width && ty >= 0 && ty < a->height)
        return a->mud[ty * a->width + tx] ? a->mud_factor : 1.0;
    return 1.0;
}

static inline double nt_clearance_px(const nt_arena *a, double px, double py)
{
    int tx = (int)floor(px / a->tile);
    int ty = (int)floor(py / a->tile);
    if (tx < 0) tx = 0; else if (tx >= a->width) tx = a->width - 1;
    if (ty < 0) ty = 0; else if (ty >= a->height) ty = a->height - 1;
    return a->clearance[ty * a->width + tx] * a->tile;
}

/* ===================================================================== */
/* геометрия (engine/geometry.py)                                        */
/* ===================================================================== */

static inline double nt_wrap_angle(double a)
{
    const double TAU = 2.0 * 3.14159265358979323846;
    const double PI = 3.14159265358979323846;
    a = fmod(a + PI, TAU);
    if (a < 0) a += TAU;
    return a - PI;
}

static inline double nt_ang_diff(double a, double b)
{
    return nt_wrap_angle(a - b);
}

/*: obb_axis_radii: радиусы проекции OBB на 4 оси SAT. */
static void nt_axis_radii(double hx, double hy, double ca, double sa, double *pre)
{
    pre[0] = hx * fabs(ca) + hy * fabs(-sa);
    pre[1] = hx * fabs(sa) + hy * fabs(ca);
    pre[2] = hx * fabs(ca * ca + sa * sa) + hy * fabs(-sa * ca + ca * sa);
    pre[3] = hx * fabs(ca * -sa + sa * ca) + hy * fabs(-sa * -sa + ca * ca);
}

#define NT_INF (1.0 / 0.0)

/*: resolve_obb_aabb — выталкивание OBB из AABB (SAT по 4 осям). */
static int nt_resolve_obb_aabb(double cx, double cy, double ca, double sa,
                               double ax0, double ay0, double ax1, double ay1,
                               const double *pre, double *out)
{
    double tcx = (ax0 + ax1) * 0.5;
    double tcy = (ay0 + ay1) * 0.5;
    double thx = (ax1 - ax0) * 0.5;
    double thy = (ay1 - ay0) * 0.5;
    double dx = cx - tcx;
    double dy = cy - tcy;
    double best = NT_INF, bnx = 0.0, bny = 0.0, overlap, proj;
    double aca, asa;

    overlap = (pre[0] + thx) - fabs(dx);
    if (overlap <= 0.0) return 0;
    if (overlap < best) {
        best = overlap;
        if (dx > 0) { bnx = 1.0; bny = 0.0; } else { bnx = -1.0; bny = 0.0; }
    }
    overlap = (pre[1] + thy) - fabs(dy);
    if (overlap <= 0.0) return 0;
    if (overlap < best) {
        best = overlap;
        if (dy > 0) { bnx = 0.0; bny = 1.0; } else { bnx = 0.0; bny = -1.0; }
    }
    aca = fabs(ca);
    asa = fabs(sa);
    proj = dx * ca + dy * sa;
    overlap = (pre[2] + thx * aca + thy * asa) - fabs(proj);
    if (overlap <= 0.0) return 0;
    if (overlap < best) {
        best = overlap;
        if (proj > 0) { bnx = ca; bny = sa; } else { bnx = -ca; bny = -sa; }
    }
    proj = dx * -sa + dy * ca;
    overlap = (pre[3] + thx * asa + thy * aca) - fabs(proj);
    if (overlap <= 0.0) return 0;
    if (overlap < best) {
        best = overlap;
        if (proj > 0) { bnx = -sa; bny = ca; } else { bnx = sa; bny = -ca; }
    }
    out[0] = best;
    out[1] = bnx;
    out[2] = bny;
    return 1;
}

/*: segment_obb — пересечение луча с повёрнутым прямоугольником. */
static int nt_segment_obb(double ox, double oy, double dx, double dy,
                          double cx, double cy, double hx, double hy,
                          double angle, double max_t, double *out)
{
    double ca = cos(angle);
    double sa = sin(angle);
    double rx = ox - cx;
    double ry = oy - cy;
    double lx = rx * ca + ry * sa;
    double ly = -rx * sa + ry * ca;
    double ldx = dx * ca + dy * sa;
    double ldy = -dx * sa + dy * ca;
    double tmin = 0.0, tmax = max_t;
    int axis = -1;
    double sign = 0.0;
    int i;
    double p, q, h, s, t1, t2, nx, ny;

    for (i = 0; i < 2; i++) {
        if (i == 0) { p = lx; q = ldx; h = hx; }
        else        { p = ly; q = ldy; h = hy; }
        if (fabs(q) < 1e-12) {
            if (fabs(p) > h) return 0;
            continue;
        }
        s = (q > 0) ? -1.0 : 1.0;
        t1 = (-h - p) / q;
        t2 = (h - p) / q;
        if (t1 > t2) { double tmp = t1; t1 = t2; t2 = tmp; }
        if (t1 > tmin) {
            tmin = t1;
            axis = i;
            sign = s;
        }
        if (t2 < tmax) tmax = t2;
        if (tmin > tmax) return 0;
    }
    if (axis < 0) {
        if (tmax < 0.0) return 0;
        if (fabs(ldx) >= fabs(ldy))
            nx = (ldx > 0.0) ? -1.0 : 1.0, ny = 0.0;
        else
            nx = 0.0, ny = (ldy > 0.0) ? -1.0 : 1.0;
        out[0] = 0.0;
        out[1] = nx * ca - ny * sa;
        out[2] = nx * sa + ny * ca;
        return 1;
    }
    if (tmin > max_t) return 0;
    if (axis == 0) { nx = sign; ny = 0.0; } else { nx = 0.0; ny = sign; }
    out[0] = tmin;
    out[1] = nx * ca - ny * sa;
    out[2] = nx * sa + ny * ca;
    return 1;
}

/* ===================================================================== */
/* видимость (engine/visibility.py)                                      */
/* ===================================================================== */

/*: _ray_grid_opaque — DDA по маске непрозрачности с рамкой из стен. */
static int nt_ray_grid_opaque(const nt_arena *a, double ox, double oy,
                              double dx, double dy, double max_t, double *out)
{
    double tile, ftile, t_delta_x, t_max_x, t_delta_y, t_max_y, t = 0.0;
    int x, y, step_x, step_y, px, py, stepped_x, stride;
    const unsigned char *pad, *row;

    if (dx != dx || dy != dy) return 0;
    tile = a->tile;
    ftile = a->tile_f;
    x = (int)floor(ox / tile);
    y = (int)floor(oy / tile);

    if (dx > 0) {
        step_x = 1;
        t_delta_x = fabs(ftile / dx);
        t_max_x = ((x + 1) * tile - ox) / dx;
    } else if (dx < 0) {
        step_x = -1;
        t_delta_x = fabs(ftile / dx);
        t_max_x = (x * tile - ox) / dx;
    } else {
        step_x = -1;
        t_delta_x = NT_INF;
        t_max_x = NT_INF;
    }
    if (dy > 0) {
        step_y = 1;
        t_delta_y = fabs(ftile / dy);
        t_max_y = ((y + 1) * tile - oy) / dy;
    } else if (dy < 0) {
        step_y = -1;
        t_delta_y = fabs(ftile / dy);
        t_max_y = (y * tile - oy) / dy;
    } else {
        step_y = -1;
        t_delta_y = NT_INF;
        t_max_y = NT_INF;
    }
    if (!(max_t >= 0.0)) return 0;

    stride = a->width + 4;
    pad = a->opaque_pad;
    px = x + 2;
    py = y + 2;
    row = pad + (size_t)py * stride;
    stepped_x = 1;
    for (;;) {
        if (t_max_x < t_max_y) {
            t = t_max_x;
            t_max_x += t_delta_x;
            px += step_x;
            stepped_x = 1;
        } else {
            t = t_max_y;
            t_max_y += t_delta_y;
            py += step_y;
            row = pad + (size_t)py * stride;
            stepped_x = 0;
        }
        if (t > max_t) break;
        /* Рамка из стен держит луч внутри маски; кламп — только страховка
         * от выхода за пределы (в Python это был бы IndexError). */
        if (py < 0 || py >= a->height + 4 || px < 0 || px >= stride) {
            out[0] = t;
            if (stepped_x) { out[1] = (double)-step_x; out[2] = 0.0; }
            else { out[1] = 0.0; out[2] = (double)-step_y; }
            return 1;
        }
        if (row[px]) {
            out[0] = t;
            if (stepped_x) { out[1] = (double)-step_x; out[2] = 0.0; }
            else { out[1] = 0.0; out[2] = (double)-step_y; }
            return 1;
        }
    }
    return 0;
}

static int nt_line_clear(const nt_arena *a, double ax, double ay,
                         double bx, double by)
{
    double d = nt_hypot(bx - ax, by - ay);
    double out[3];
    if (d < 1e-6) return !nt_opaque_px(a, ax, ay);
    if (nt_ray_grid_opaque(a, ax, ay, (bx - ax) / d, (by - ay) / d, d - 0.5, out))
        return 0;
    return 1;
}

/*: hull_los — виден ли корпус целиком хотя бы частично. */
static int nt_hull_los(const nt_arena *a, double ox, double oy, double tx,
                       double ty, double half_len, double half_wid, double angle)
{
    double ca, sa, px, py;
    int sgn;
    if (nt_line_clear(a, ox, oy, tx, ty)) return 1;
    ca = cos(angle);
    sa = sin(angle);
    for (sgn = 1; sgn >= -1; sgn -= 2) {
        px = tx + (double)sgn * half_len * ca;
        py = ty + (double)sgn * half_len * sa;
        if (nt_line_clear(a, ox, oy, px, py)) return 1;
    }
    for (sgn = 1; sgn >= -1; sgn -= 2) {
        px = tx + (double)sgn * half_wid * -sa;
        py = ty + (double)sgn * half_wid * ca;
        if (nt_line_clear(a, ox, oy, px, py)) return 1;
    }
    return 0;
}

/* ===================================================================== */
/* броня (engine/armor.py)                                               */
/* ===================================================================== */

typedef struct {
    double damage;
    int face;          /* 0 front, 1 side, 2 rear */
    double theta_deg;
    int ricochet;
} nt_impact;

static nt_impact nt_resolve_impact(double nx, double ny, double dirx, double diry,
                                   double hull_angle, double front_half_angle,
                                   double ric_front, double ric_side,
                                   double ric_rear, double base_damage,
                                   double ricochet_damage)
{
    nt_impact imp;
    double nlen = nt_hypot(nx, ny);
    double cos_theta, theta, rel;
    int face;
    double limit;

    if (nlen == 0.0) nlen = 1.0;
    nx = nx / nlen;
    ny = ny / nlen;
    cos_theta = -(dirx * nx + diry * ny);
    if (cos_theta < 0.0) cos_theta = 0.0;
    if (cos_theta > 1.0) cos_theta = 1.0;
    theta = acos(cos_theta) * (180.0 / 3.14159265358979323846);

    rel = fabs(nt_ang_diff(atan2(ny, nx), hull_angle) * (180.0 / 3.14159265358979323846));
    if (rel <= front_half_angle) face = 0;
    else if (rel >= 180.0 - front_half_angle) face = 2;
    else face = 1;

    limit = (face == 0) ? ric_front : ((face == 1) ? ric_side : ric_rear);
    if (!(limit != limit) && theta >= limit) {     /* NaN → порога нет */
        imp.damage = ricochet_damage;
        imp.face = face;
        imp.theta_deg = theta;
        imp.ricochet = 1;
        return imp;
    }
    imp.damage = base_damage;
    imp.face = face;
    imp.theta_deg = theta;
    imp.ricochet = 0;
    return imp;
}

/* ===================================================================== */
/* мир (engine/world.py, engine/tank.py, engine/projectile.py)           */
/* ===================================================================== */

#define RICOCHET_SPEED_LOSS 1.0
#define RICOCHET_PUSH 2.0
#define RICOCHET_SKIP_TIME 0.08

enum { NT_OUTCOME_NONE = 0, NT_OUTCOME_DRAW = 1, NT_OUTCOME_A = 2, NT_OUTCOME_B = 3 };
enum { NT_REASON_NONE = 0, NT_REASON_DESTROYED = 1, NT_REASON_TIMEOUT = 2,
       NT_REASON_MUTUAL = 3 };

typedef struct {
    int id;
    double x, y, hull, turret, vx, vy, hp, hp_max, mass, cooldown, ram_cd;
    int alive;
    double half_x, half_y;

    long shots, hits, ricochets, ricochets_taken, rams;
    double damage_dealt, damage_taken;

    int sees_enemy;
    double sight[7];
} nt_tank;

typedef struct {
    int owner;
    double x, y, dx, dy, life, speed;
    int alive;
    int skip;
} nt_bullet;

typedef struct {
    nt_arena arena;
    nt_tank tanks[2];
    nt_bullet bullets[NT_MAX_BULLETS];
    int nbullets;
    long tick;
    long max_ticks;
    int tick_rate;
    double dt;
    double dt_rounded;
    nt_rng rng;

    double hp, hull_len, hull_wid, speed_fwd, speed_rev, accel, decel;
    double hull_turn, turret_turn;
    double reload, bullet_speed, bullet_life, bullet_radius, spread, muzzle_offset;
    double base_damage;
    int self_damage;
    double front_half_angle, ric_front, ric_side, ric_rear, ricochet_damage;
    double wall_ricochet_angle;
    double ram_damage, ram_speed, ram_cooldown, ram_restitution, mass;
    double view_range, view_cone, bumper_range;
    double half_far_x, half_far_y;

    int over;
    int outcome;
    int end_reason;
} nt_sim;

/* --- порядок параметров: см. PARAMS в engine/native/api.py --- */
enum {
    P_HP = 0, P_HULL_LEN, P_HULL_WID, P_SPEED_FWD, P_SPEED_REV, P_ACCEL, P_DECEL,
    P_HULL_TURN, P_TURRET_TURN, P_RELOAD, P_BULLET_SPEED, P_BULLET_LIFE,
    P_BULLET_RADIUS, P_SPREAD, P_MUZZLE_OFFSET, P_BASE_DAMAGE, P_SELF_DAMAGE,
    P_FRONT_HALF_ANGLE, P_RIC_FRONT, P_RIC_SIDE, P_RIC_REAR,
    P_RICOCHET_DAMAGE, P_WALL_RICOCHET_ANGLE, P_RAM_DAMAGE, P_RAM_SPEED,
    P_RAM_COOLDOWN, P_RAM_RESTITUTION, P_MASS, P_VIEW_RANGE, P_VIEW_CONE,
    P_BUMPER_RANGE, P_COUNT
};

static void nt_take_damage(nt_tank *t, double amount)
{
    double hp = nt_round(t->hp - amount, 4);
    t->hp = (hp < 0.0) ? 0.0 : hp;
    t->damage_taken += amount;
    if (t->hp <= 0.0) t->alive = 0;
}

/* --- push_out (engine/grid.py) ----------------------------------------- */

/*: _push_out_near: перебор кандидатов из окрестности 3x3 (Arena.near_blocks).
 *  Возвращает признак контакта, координаты — через pcx/pcy. */
static int nt_push_out_near(const nt_arena *a, double *pcx, double *pcy,
                            double hx, double hy, int iterations,
                            double ca, double sa, int ctx, int cty)
{
    double tile = a->tile;
    int width = a->width, height = a->height;
    double cx = *pcx, cy = *pcy;
    double pre[4];
    int have_pre = 0, hit = 0, it;

    if (!(ctx >= 0 && ctx < width)) ctx = (ctx < 0) ? 0 : width - 1;
    if (!(cty >= 0 && cty < height)) cty = (cty < 0) ? 0 : height - 1;

    for (it = 0; it < iterations; it++) {
        int off = a->near_off[cty * width + ctx];
        int cnt = a->near_off[cty * width + ctx + 1] - off;
        double best_depth = 0.0, bnx = 0.0, bny = 0.0;
        int have_best = 0, k;
        double r1, r2, aabb_x0, aabb_x1, aabb_y0, aabb_y1, res[3];

        if (cnt == 0) break;
        if (!have_pre) { nt_axis_radii(hx, hy, ca, sa, pre); have_pre = 1; }
        r1 = pre[0];
        r2 = pre[1];
        aabb_x0 = cx - r1;
        aabb_x1 = cx + r1;
        aabb_y0 = cy - r2;
        aabb_y1 = cy + r2;
        for (k = 0; k < cnt; k++) {
            const double *box = a->near_box + (size_t)(off + k) * 4;
            if (box[0] >= aabb_x1 || box[2] <= aabb_x0 ||
                box[1] >= aabb_y1 || box[3] <= aabb_y0)
                continue;
            if (!nt_resolve_obb_aabb(cx, cy, ca, sa,
                                     box[0], box[1], box[2], box[3], pre, res))
                continue;
            if (!have_best || res[0] > best_depth) {
                best_depth = res[0];
                bnx = res[1];
                bny = res[2];
                have_best = 1;
            }
        }
        if (!have_best) break;
        cx += bnx * best_depth;
        cy += bny * best_depth;
        hit = 1;
        ctx = (int)floor(cx / tile);
        cty = (int)floor(cy / tile);
        if (!(ctx >= 0 && ctx < width)) ctx = (ctx < 0) ? 0 : width - 1;
        if (!(cty >= 0 && cty < height)) cty = (cty < 0) ? 0 : height - 1;
    }
    *pcx = cx;
    *pcy = cy;
    return hit;
}

/*: _push_out_region: медленный путь — перебор всех непроходимых тайлов в
 *  рамке AABB. Нужен, когда корпус больше тайла. */
static int nt_push_out_region(const nt_arena *a, double *pcx, double *pcy,
                              double hx, double hy, int iterations,
                              double ca, double sa)
{
    double tile = a->tile;
    double ex, ey, cx = *pcx, cy = *pcy;
    double pre[4];
    int have_pre = 0, hit = 0, it;
    int width = a->width, height = a->height;

    ex = hx * fabs(ca) + hy * fabs(sa);
    ey = hx * fabs(sa) + hy * fabs(ca);

    for (it = 0; it < iterations; it++) {
        int tx0 = (int)floor((cx - ex) / tile);
        int tx1 = (int)floor((cx + ex) / tile);
        int ty0 = (int)floor((cy - ey) / tile);
        int ty1 = (int)floor((cy + ey) / tile);
        double best_depth = 0.0, bnx = 0.0, bny = 0.0, res[3];
        int have_best = 0, ty;

        if (tx0 < 0) tx0 = 0;
        if (tx1 > width - 1) tx1 = width - 1;
        if (ty0 < 0) ty0 = 0;
        if (ty1 > height - 1) ty1 = height - 1;

        for (ty = ty0; ty <= ty1; ty++) {
            int lo = a->cols_off[ty];
            int hi = a->cols_off[ty + 1];
            int i, ay0, ay1;
            if (lo == hi) continue;
            /* bisect_left(cols, tx0) */
            {
                int L = lo, R = hi;
                while (L < R) {
                    int mid = (L + R) / 2;
                    if (a->cols[mid] < tx0) L = mid + 1; else R = mid;
                }
                i = L;
            }
            if (i >= hi) continue;
            ay0 = ty * a->tile;
            ay1 = ay0 + a->tile;
            while (i < hi) {
                int tx = a->cols[i];
                if (tx > tx1) break;
                i++;
                if (!have_pre) { nt_axis_radii(hx, hy, ca, sa, pre); have_pre = 1; }
                if (!nt_resolve_obb_aabb(cx, cy, ca, sa,
                                         tx * a->tile, ay0,
                                         tx * a->tile + a->tile, ay1, pre, res))
                    continue;
                if (!have_best || res[0] > best_depth) {
                    best_depth = res[0];
                    bnx = res[1];
                    bny = res[2];
                    have_best = 1;
                }
            }
        }
        if (!have_best) break;
        cx += bnx * best_depth;
        cy += bny * best_depth;
        hit = 1;
    }
    *pcx = cx;
    *pcy = cy;
    return hit;
}

/*: grid.push_out с признаком столкновения (в Python это третий элемент). */
static int nt_push_out_hit(const nt_arena *a, double *pcx, double *pcy,
                           double hx, double hy, double ca, double sa)
{
    double tile = a->tile;
    double aca = fabs(ca), asa = fabs(sa);
    double ex = hx * aca + hy * asa;
    double ey = hx * asa + hy * aca;
    double cx = *pcx, cy = *pcy;

    if (ex < tile && ey < tile) {
        int ctx = (int)floor(cx / tile);
        int cty = (int)floor(cy / tile);
        int inside = ctx >= 0 && ctx < a->width && cty >= 0 && cty < a->height;
        if (inside || (cx + ex > 0.0 && cx - ex < a->pixel_width &&
                       cy + ey > 0.0 && cy - ey < a->pixel_height)) {
            return nt_push_out_near(a, pcx, pcy, hx, hy, 4, ca, sa, ctx, cty);
        }
    }
    return nt_push_out_region(a, pcx, pcy, hx, hy, 4, ca, sa);
}

/* --- шаг танка (engine/tank.py::Tank.step) ------------------------------ */

static double nt_obb_span(const nt_tank *t, double ux, double uy)
{
    double ca = cos(t->hull);
    double sa = sin(t->hull);
    return t->half_x * fabs(ca * ux + sa * uy) + t->half_y * fabs(-sa * ux + ca * uy);
}

static int nt_tank_step(nt_sim *s, nt_tank *t, double drive_cmd, double turn_cmd,
                        double turret_cmd)
{
    const double TAU = 2.0 * 3.14159265358979323846;
    const double PI = 3.14159265358979323846;
    double dt = s->dt;
    double angle, left, right, a_left, a_right, span, drive, want;
    double fx, fy, tvx, tvy, rate, dvx, dvy, dlen, limit, k, vx, vy, sp, fwd;
    double x, y, hx, hy, pad, lo_x, hi_x, lo_y, hi_y, nx, ny, cx_, cy_, nlen;
    double ux, uy, dot;
    int hit;

    /* 1. башня */
    angle = t->turret + turret_cmd * s->turret_turn * dt;
    angle = fmod(angle + PI, TAU);
    if (angle < 0) angle += TAU;
    t->turret = angle - PI;

    /* 2. гусеницы */
    left = drive_cmd + turn_cmd;
    right = drive_cmd - turn_cmd;
    a_left = fabs(left);
    a_right = fabs(right);
    span = (a_left > a_right) ? a_left : a_right;
    if (span > 1.0) {
        left /= span;
        right /= span;
    }
    drive = (left + right) * 0.5;
    want = drive * (drive >= 0 ? s->speed_fwd : s->speed_rev);
    if (s->arena.has_mud) want *= nt_speed_factor(&s->arena, t->x, t->y);
    angle = t->hull + (left - right) * 0.5 * s->hull_turn * dt;
    angle = fmod(angle + PI, TAU);
    if (angle < 0) angle += TAU;
    t->hull = angle - PI;
    fx = cos(t->hull);
    fy = sin(t->hull);
    tvx = fx * want;
    tvy = fy * want;

    /* 3. разгон/торможение */
    rate = (fabs(want) > 1e-6) ? s->accel : s->decel;
    dvx = tvx - t->vx;
    dvy = tvy - t->vy;
    dlen = nt_hypot(dvx, dvy);
    limit = rate * dt;
    if (dlen > limit) {
        k = limit / dlen;
        dvx *= k;
        dvy *= k;
    }
    vx = t->vx + dvx;
    vy = t->vy + dvy;
    sp = nt_hypot(vx, vy);
    fwd = s->speed_fwd;
    if (sp > fwd) {
        k = fwd / sp;
        vx *= k;
        vy *= k;
    }
    t->vx = vx;
    t->vy = vy;

    /* 4. перемещение и расталкивание стенами */
    x = t->x + vx * dt;
    y = t->y + vy * dt;
    hx = t->half_x;
    hy = t->half_y;
    pad = ((hx > hy) ? hx : hy) + 1.0;
    lo_x = pad;
    hi_x = s->arena.pixel_width - pad;
    lo_y = pad;
    hi_y = s->arena.pixel_height - pad;
    if (x < lo_x || x > hi_x) {
        x = (x < lo_x) ? lo_x : hi_x;
        t->vx = 0.0;
        vx = 0.0;
    }
    if (y < lo_y || y > hi_y) {
        y = (y < lo_y) ? lo_y : hi_y;
        t->vy = 0.0;
        vy = 0.0;
    }

    nx = x;
    ny = y;
    hit = nt_push_out_hit(&s->arena, &nx, &ny, hx, hy, fx, fy);
    if (hit) {
        cx_ = nx - x;
        cy_ = ny - y;
        nlen = nt_hypot(cx_, cy_);
        if (nlen > 1e-9) {
            ux = cx_ / nlen;
            uy = cy_ / nlen;
            dot = t->vx * ux + t->vy * uy;
            if (dot < 0.0) {
                t->vx -= ux * dot;
                t->vy -= uy * dot;
            }
        }
        x = nx;
        y = ny;
    }
    t->x = x;
    t->y = y;

    /* 5. перезарядка и пауза после тарана */
    t->cooldown = (t->cooldown > 1e-6) ? ((t->cooldown - dt > 0.0) ? t->cooldown - dt : 0.0) : 0.0;
    t->ram_cd = (t->ram_cd > 1e-6) ? ((t->ram_cd - dt > 0.0) ? t->ram_cd - dt : 0.0) : 0.0;
    return hit;
}

/* --- видимость обоих танков -------------------------------------------- */

static void nt_can_see(const nt_arena *a, double ox, double oy, double facing,
                       double tx, double ty, double t_half_len, double t_half_wid,
                       double t_angle, double view_range, double view_cone,
                       double bumper_range, double *out)
{
    double d = nt_hypot(tx - ox, ty - oy);
    double bearing = atan2(ty - oy, tx - ox);
    int in_range = d <= view_range;
    int in_cone = 1;
    int bumper = d <= bumper_range;
    int los;

    if (view_cone < 180.0) {
        double rel = nt_ang_diff(bearing, facing) * (180.0 / 3.14159265358979323846);
        in_cone = fabs(rel) <= view_cone;
    }
    if (!in_range || !(in_cone || bumper)) {
        out[0] = 0.0;
        out[1] = (double)in_cone;
        out[2] = (double)in_range;
        out[3] = 0.0;
        out[4] = (double)bumper;
        out[5] = bearing;
        out[6] = d;
        return;
    }
    los = nt_hull_los(a, ox, oy, tx, ty, t_half_len, t_half_wid, t_angle);
    out[0] = (double)los;
    out[1] = (double)in_cone;
    out[2] = (double)in_range;
    out[3] = (double)los;
    out[4] = (double)bumper;
    out[5] = bearing;
    out[6] = d;
}

static void nt_update_vision(nt_sim *s)
{
    nt_tank *a = &s->tanks[0];
    nt_tank *b = &s->tanks[1];
    double d[7];
    int alive_b, alive_a;

    nt_can_see(&s->arena, a->x, a->y, a->turret, b->x, b->y,
               b->half_x, b->half_y, b->hull,
               s->view_range, s->view_cone, s->bumper_range, d);
    alive_b = b->alive;
    a->sees_enemy = ((int)d[0]) && alive_b;
    a->sight[0] = a->sees_enemy ? 1.0 : 0.0;
    a->sight[1] = d[1];
    a->sight[2] = d[2];
    a->sight[3] = d[3];
    a->sight[4] = d[4];
    a->sight[5] = d[5];
    a->sight[6] = d[6];

    nt_can_see(&s->arena, b->x, b->y, b->turret, a->x, a->y,
               a->half_x, a->half_y, a->hull,
               s->view_range, s->view_cone, s->bumper_range, d);
    alive_a = a->alive;
    b->sees_enemy = ((int)d[0]) && alive_a;
    b->sight[0] = b->sees_enemy ? 1.0 : 0.0;
    b->sight[1] = d[1];
    b->sight[2] = d[2];
    b->sight[3] = d[3];
    b->sight[4] = d[4];
    b->sight[5] = d[5];
    b->sight[6] = d[6];
}

/* --- свободный спавн ---------------------------------------------------- */

static void nt_free_spawn(nt_sim *s, nt_tank *t)
{
    const double PI = 3.14159265358979323846;
    double x = t->x, y = t->y;
    double ca = cos(t->hull), sa = sin(t->hull);
    int hit = nt_push_out_hit(&s->arena, &x, &y, t->half_x, t->half_y, ca, sa);
    double probe = 40.0, fx, fy, best_clear, best;
    int k;

    if (hit) { t->x = x; t->y = y; }
    fx = t->x + cos(t->hull) * probe;
    fy = t->y + sin(t->hull) * probe;
    if (nt_clearance_px(&s->arena, fx, fy) >= 24.0) return;
    best = t->hull;
    best_clear = -1.0;
    for (k = 0; k < 16; k++) {
        double ang = PI * 2 * k / 16;
        double clear = nt_clearance_px(&s->arena, t->x + cos(ang) * probe,
                                       t->y + sin(ang) * probe);
        if (clear > best_clear) {
            best = ang;
            best_clear = clear;
        }
    }
    t->hull = best;
    t->turret = best;
}

/* --- снаряды ------------------------------------------------------------ */

static double nt_rand_spread(nt_sim *s)
{
    const double PI = 3.14159265358979323846;
    double rad = s->spread * (PI / 180.0);
    return rad * (nt_rng_random(&s->rng) * 2.0 - 1.0);
}

static void nt_spawn_bullet(nt_sim *s, nt_tank *t)
{
    double mx, my, angle, dx, dy;
    nt_bullet *b;

    if (s->nbullets >= NT_MAX_BULLETS) return;
    mx = t->x + cos(t->turret) * s->muzzle_offset;
    my = t->y + sin(t->turret) * s->muzzle_offset;
    angle = t->turret + nt_rand_spread(s);
    dx = cos(angle);
    dy = sin(angle);
    t->cooldown = s->reload;
    t->shots += 1;
    b = &s->bullets[s->nbullets++];
    b->owner = t->id;
    b->x = mx;
    b->y = my;
    b->dx = dx;
    b->dy = dy;
    b->life = s->bullet_life;
    b->speed = s->bullet_speed;
    b->alive = 1;
    b->skip = -1;
}

static int nt_near_tank(const nt_sim *s, const nt_bullet *b, const nt_tank *tank)
{
    double hx = s->half_far_x, hy = s->half_far_y;
    double pad, m;
    if (tank == NULL || !tank->alive) return 0;
    m = (hx > hy) ? hx : hy;
    pad = m + b->speed * RICOCHET_SKIP_TIME;
    return nt_hypot(b->x - tank->x, b->y - tank->y) < pad;
}

static void nt_step_bullet(nt_sim *s, nt_bullet *b)
{
    double step, ox, oy, t_wall;
    double res_wall[3];
    int has_wall, hit_t_ok = 0, k;
    double hit_t = 0.0;
    nt_tank *target = NULL;
    double normal[2] = {0.0, 0.0};

    /* advance */
    b->x += b->dx * b->speed * s->dt;
    b->y += b->dy * b->speed * s->dt;
    b->life -= s->dt;
    if (b->life <= 0) b->alive = 0;

    step = b->speed * s->dt;
    ox = b->x - b->dx * step;
    oy = b->y - b->dy * step;
    has_wall = nt_ray_grid_opaque(&s->arena, ox, oy, b->dx, b->dy, step, res_wall);
    t_wall = has_wall ? res_wall[0] : 0.0;

    for (k = 0; k < 2; k++) {
        nt_tank *t = &s->tanks[k];
        double res[3];
        if (!t->alive) continue;
        if (t->id == b->owner && !s->self_damage) continue;
        if (t->id == b->skip) continue;
        if (!nt_segment_obb(ox, oy, b->dx, b->dy, t->x, t->y,
                            s->half_far_x, s->half_far_y, t->hull, step, res))
            continue;
        if (!hit_t_ok || res[0] < hit_t) {
            hit_t = res[0];
            target = t;
            normal[0] = res[1];
            normal[1] = res[2];
            hit_t_ok = 1;
        }
    }

    if (target != NULL && (!has_wall || hit_t < t_wall)) {
        double hx = ox + b->dx * hit_t;
        double hy = oy + b->dy * hit_t;
        nt_impact imp = nt_resolve_impact(normal[0], normal[1], b->dx, b->dy,
                                          target->hull, s->front_half_angle,
                                          s->ric_front, s->ric_side, s->ric_rear,
                                          s->base_damage, s->ricochet_damage);
        if (imp.ricochet) {
            double nx = normal[0], ny = normal[1];
            double dot = b->dx * nx + b->dy * ny;
            b->dx -= 2.0 * dot * nx;
            b->dy -= 2.0 * dot * ny;
            b->speed *= RICOCHET_SPEED_LOSS;
            b->x = hx + b->dx * RICOCHET_PUSH;
            b->y = hy + b->dy * RICOCHET_PUSH;
            b->skip = target->id;
            target->ricochets_taken += 1;
            /* _dict_event: рикошет идёт в счётчик стрелка */
            s->tanks[b->owner].ricochets += 1;
            return;
        }
        b->alive = 0;
        nt_take_damage(target, imp.damage);
        s->tanks[b->owner].hits += 1;
        s->tanks[b->owner].damage_dealt += imp.damage;
        return;
    }

    if (has_wall) {
        double wx = ox + b->dx * t_wall;
        double wy = oy + b->dy * t_wall;
        double nx = res_wall[1], ny = res_wall[2];
        double cos_theta = -(b->dx * nx + b->dy * ny);
        double theta;
        if (cos_theta < 0.0) cos_theta = 0.0;
        if (cos_theta > 1.0) cos_theta = 1.0;
        theta = acos(cos_theta) * (180.0 / 3.14159265358979323846);
        if (theta >= s->wall_ricochet_angle) {
            double dot = b->dx * nx + b->dy * ny;
            b->dx -= 2.0 * dot * nx;
            b->dy -= 2.0 * dot * ny;
            b->speed *= RICOCHET_SPEED_LOSS;
            b->x = wx + b->dx * RICOCHET_PUSH;
            b->y = wy + b->dy * RICOCHET_PUSH;
            return;
        }
        b->alive = 0;
        return;
    }

    if (b->skip >= 0 && !nt_near_tank(s, b, &s->tanks[b->skip])) b->skip = -1;
}

/* --- таран -------------------------------------------------------------- */

static void nt_resolve_ram(nt_sim *s)
{
    nt_tank *a = &s->tanks[0];
    nt_tank *b = &s->tanks[1];
    double d, ux, uy, pen, closing, ma, mb, j, hxa, hya, hxb, hyb, dmg;

    if (!a->alive || !b->alive) return;
    d = nt_hypot(b->x - a->x, b->y - a->y);
    if (d < 1e-6) return;
    hxa = a->half_x; hya = a->half_y;
    hxb = b->half_x; hyb = b->half_y;
    if (d >= 1.4143 * (((hxa > hya) ? hxa : hya) + ((hxb > hyb) ? hxb : hyb)))
        return;
    ux = (b->x - a->x) / d;
    uy = (b->y - a->y) / d;
    pen = nt_obb_span(a, ux, uy) + nt_obb_span(b, ux, uy) - d;
    if (pen <= 0.0) return;
    closing = (a->vx - b->vx) * ux + (a->vy - b->vy) * uy;
    ma = (a->mass > 1e-6) ? a->mass : 1e-6;
    mb = (b->mass > 1e-6) ? b->mass : 1e-6;

    a->x -= ux * pen * 0.15;
    a->y -= uy * pen * 0.15;
    b->x += ux * pen * 0.15;
    b->y += uy * pen * 0.15;
    if (closing > 0.0) {
        j = (1.0 + s->ram_restitution) * closing / (1.0 / ma + 1.0 / mb);
        a->vx -= j * ux / ma;
        a->vy -= j * uy / ma;
        b->vx += j * ux / mb;
        b->vy += j * uy / mb;
    }
    if (closing <= s->ram_speed) return;
    if (a->ram_cd > 0.0 || b->ram_cd > 0.0) return;
    dmg = s->ram_damage;
    a->ram_cd = b->ram_cd = s->ram_cooldown;
    nt_take_damage(a, dmg);
    nt_take_damage(b, dmg);
    a->rams += 1;
    b->rams += 1;
}

/* --- конец боя ---------------------------------------------------------- */

static void nt_finish(nt_sim *s)
{
    nt_tank *a = &s->tanks[0];
    nt_tank *b = &s->tanks[1];
    if (s->over) return;
    if (a->alive && b->alive && s->tick >= s->max_ticks) {
        s->over = 1; s->outcome = NT_OUTCOME_DRAW; s->end_reason = NT_REASON_TIMEOUT;
    } else if (!a->alive && !b->alive) {
        s->over = 1; s->outcome = NT_OUTCOME_DRAW; s->end_reason = NT_REASON_MUTUAL;
    } else if (!b->alive) {
        s->over = 1; s->outcome = NT_OUTCOME_A; s->end_reason = NT_REASON_DESTROYED;
    } else if (!a->alive) {
        s->over = 1; s->outcome = NT_OUTCOME_B; s->end_reason = NT_REASON_DESTROYED;
    }
}

/* --- буфер наблюдения --------------------------------------------------- */

static void nt_fill_obs(nt_sim *s, double *out)
{
    int i, base;
    double time_rounded = nt_round((double)s->tick * s->dt, 5);

    out[0] = (double)s->tick;
    out[1] = time_rounded;
    out[2] = (double)s->over;
    out[3] = (double)s->outcome;
    out[4] = (double)s->end_reason;
    for (i = 0; i < 2; i++) {
        nt_tank *t = &s->tanks[i];
        base = 5 + i * 11;
        out[base + 0] = nt_round(t->x, 2);
        out[base + 1] = nt_round(t->y, 2);
        out[base + 2] = nt_round(t->hull, 5);
        out[base + 3] = nt_round(t->turret, 5);
        out[base + 4] = nt_round(t->vx, 2);
        out[base + 5] = nt_round(t->vy, 2);
        out[base + 6] = nt_round(t->hp, 3);
        out[base + 7] = t->hp_max;
        out[base + 8] = (t->cooldown <= 1e-6) ? 1.0 : 0.0;
        out[base + 9] = nt_round(t->cooldown, 4);
        out[base + 10] = s->reload;
    }
    /* 5 + 22 = 27 */
    for (i = 0; i < 2; i++) {
        nt_tank *t = &s->tanks[i];
        nt_tank *foe = &s->tanks[1 - i];
        base = 27 + i * 8;
        out[base + 0] = (t->sees_enemy && foe->alive) ? 1.0 : 0.0;
        memcpy(out + base + 1, t->sight, 7 * sizeof(double));
    }
    /* 27 + 16 = 43 */
    out[43] = s->dt_rounded;
    out[44] = (double)s->nbullets;
}

/* ===================================================================== */
/* публичный интерфейс                                                    */
/* ===================================================================== */

static void nt_free_arena(nt_arena *a)
{
    free(a->opaque);
    free(a->block);
    free(a->mud);
    free(a->opaque_pad);
    free(a->cols_off);
    free(a->cols);
    free(a->near_off);
    free(a->near_box);
    free(a->clearance);
    memset(a, 0, sizeof(*a));
}

static void nt_build_arena(nt_arena *a, const unsigned char *opaque,
                           const unsigned char *block, const unsigned char *mud,
                           const double *clearance, int width, int height,
                           int tile, double mud_factor)
{
    int x, y, stride = width + 4;
    int ncols = 0, nnear = 0, off;

    memset(a, 0, sizeof(*a));
    a->width = width;
    a->height = height;
    a->tile = tile;
    a->tile_f = (double)tile;
    a->pixel_width = (double)(width * tile);
    a->pixel_height = (double)(height * tile);
    a->mud_factor = mud_factor;

    a->opaque = (unsigned char *)malloc((size_t)(width * height));
    a->block = (unsigned char *)malloc((size_t)(width * height));
    a->mud = (unsigned char *)malloc((size_t)(width * height));
    a->clearance = (double *)malloc(sizeof(double) * (size_t)(width * height));
    memcpy(a->opaque, opaque, (size_t)(width * height));
    memcpy(a->block, block, (size_t)(width * height));
    memcpy(a->mud, mud, (size_t)(width * height));
    memcpy(a->clearance, clearance, sizeof(double) * (size_t)(width * height));

    a->has_mud = 0;
    for (x = 0; x < width * height; x++) {
        if (a->mud[x]) { a->has_mud = 1; break; }
    }

    a->opaque_pad = (unsigned char *)malloc((size_t)(stride * (height + 4)));
    memset(a->opaque_pad, 1, (size_t)(stride * (height + 4)));
    for (y = 0; y < height; y++)
        for (x = 0; x < width; x++)
            a->opaque_pad[(size_t)(y + 2) * stride + (x + 2)] = a->opaque[y * width + x];

    for (y = 0; y < height; y++)
        for (x = 0; x < width; x++)
            if (a->block[y * width + x]) ncols++;
    a->cols_off = (int *)malloc(sizeof(int) * (size_t)(height + 1));
    a->cols = (int *)malloc(sizeof(int) * (size_t)(ncols ? ncols : 1));
    off = 0;
    for (y = 0; y < height; y++) {
        a->cols_off[y] = off;
        for (x = 0; x < width; x++)
            if (a->block[y * width + x]) a->cols[off++] = x;
    }
    a->cols_off[height] = off;

    for (y = 0; y < height; y++) {
        int y_from = (y > 0) ? y - 1 : 0;
        int y_to = (y < height - 1) ? y + 2 : height;
        for (x = 0; x < width; x++) {
            int x_from = (x > 0) ? x - 1 : 0;
            int x_to = (x < width - 1) ? x + 2 : width;
            int yy, xx;
            for (yy = y_from; yy < y_to; yy++)
                for (xx = x_from; xx < x_to; xx++)
                    if (a->block[yy * width + xx]) nnear++;
        }
    }
    a->near_off = (int *)malloc(sizeof(int) * (size_t)(width * height + 1));
    a->near_box = (double *)malloc(sizeof(double) * (size_t)(nnear > 0 ? nnear * 4 : 1));
    off = 0;
    for (y = 0; y < height; y++) {
        int y_from = (y > 0) ? y - 1 : 0;
        int y_to = (y < height - 1) ? y + 2 : height;
        for (x = 0; x < width; x++) {
            int x_from = (x > 0) ? x - 1 : 0;
            int x_to = (x < width - 1) ? x + 2 : width;
            int yy, xx;
            a->near_off[y * width + x] = off / 4;
            for (yy = y_from; yy < y_to; yy++) {
                for (xx = x_from; xx < x_to; xx++) {
                    if (!a->block[yy * width + xx]) continue;
                    a->near_box[off + 0] = (double)(xx * tile);
                    a->near_box[off + 1] = (double)(yy * tile);
                    a->near_box[off + 2] = (double)(xx * tile + tile);
                    a->near_box[off + 3] = (double)(yy * tile + tile);
                    off += 4;
                }
            }
        }
    }
    a->near_off[width * height] = off / 4;
}

/*: Создание боя. spawns — 6 doubles (x, y, angle) для A и B. */
void *sim_create(const unsigned char *opaque, const unsigned char *block,
                 const unsigned char *mud, const double *clearance,
                 const double *spawns, int width, int height, int tile,
                 double mud_factor, const double *p, long long seed,
                 int tick_rate, int max_seconds, double *obs_out)
{
    nt_sim *s = (nt_sim *)calloc(1, sizeof(nt_sim));
    int i;
    if (!s) return NULL;
    nt_build_arena(&s->arena, opaque, block, mud, clearance, width, height, tile, mud_factor);

    s->hp = p[P_HP];
    s->hull_len = p[P_HULL_LEN];
    s->hull_wid = p[P_HULL_WID];
    s->speed_fwd = p[P_SPEED_FWD];
    s->speed_rev = p[P_SPEED_REV];
    s->accel = p[P_ACCEL];
    s->decel = p[P_DECEL];
    s->hull_turn = p[P_HULL_TURN];
    s->turret_turn = p[P_TURRET_TURN];
    s->reload = p[P_RELOAD];
    s->bullet_speed = p[P_BULLET_SPEED];
    s->bullet_life = p[P_BULLET_LIFE];
    s->bullet_radius = p[P_BULLET_RADIUS];
    s->spread = p[P_SPREAD];
    s->muzzle_offset = p[P_MUZZLE_OFFSET];
    s->base_damage = p[P_BASE_DAMAGE];
    s->self_damage = (p[P_SELF_DAMAGE] != 0.0);
    s->front_half_angle = p[P_FRONT_HALF_ANGLE];
    s->ric_front = p[P_RIC_FRONT];
    s->ric_side = p[P_RIC_SIDE];
    s->ric_rear = p[P_RIC_REAR];
    s->ricochet_damage = p[P_RICOCHET_DAMAGE];
    s->wall_ricochet_angle = p[P_WALL_RICOCHET_ANGLE];
    s->ram_damage = p[P_RAM_DAMAGE];
    s->ram_speed = p[P_RAM_SPEED];
    s->ram_cooldown = p[P_RAM_COOLDOWN];
    s->ram_restitution = p[P_RAM_RESTITUTION];
    s->mass = p[P_MASS];
    s->view_range = p[P_VIEW_RANGE];
    s->view_cone = p[P_VIEW_CONE];
    s->bumper_range = p[P_BUMPER_RANGE];
    s->half_far_x = s->hull_len / 2.0 + s->bullet_radius;
    s->half_far_y = s->hull_wid / 2.0 + s->bullet_radius;

    s->tick_rate = tick_rate;
    s->dt = 1.0 / (double)tick_rate;
    s->dt_rounded = nt_round(s->dt, 5);
    s->max_ticks = (long)tick_rate * (long)max_seconds;
    s->tick = 0;
    nt_rng_seed(&s->rng, seed);

    for (i = 0; i < 2; i++) {
        nt_tank *t = &s->tanks[i];
        memset(t, 0, sizeof(*t));
        t->id = i;
        t->x = spawns[i * 3 + 0];
        t->y = spawns[i * 3 + 1];
        t->hull = spawns[i * 3 + 2];
        t->turret = spawns[i * 3 + 2];
        t->vx = 0.0;
        t->vy = 0.0;
        t->hp_max = s->hp;
        t->hp = s->hp;
        t->mass = s->mass;
        t->alive = 1;
        t->cooldown = 0.0;
        t->ram_cd = 0.0;
        t->half_x = s->hull_len / 2.0;
        t->half_y = s->hull_wid / 2.0;
        nt_free_spawn(s, t);
    }
    nt_update_vision(s);
    if (obs_out) nt_fill_obs(s, obs_out);
    return (void *)s;
}

/*: Один такт. act — 8 чисел: drive, turn, turret, fire × 2. */
int sim_step(void *handle, const double *act, double *obs_out)
{
    nt_sim *s = (nt_sim *)handle;
    int i, j, n;
    double dt = s->dt;

    s->tick += 1;

    /* 1. движение по командам */
    for (i = 0; i < 2; i++) {
        nt_tank *t = &s->tanks[i];
        if (!t->alive) continue;
        nt_tank_step(s, t, act[i * 4 + 0], act[i * 4 + 1], act[i * 4 + 2]);
    }

    /* 2. тараны */
    nt_resolve_ram(s);

    /* 3. выстрелы */
    for (i = 0; i < 2; i++) {
        nt_tank *t = &s->tanks[i];
        double mx, my;
        if (!t->alive || act[i * 4 + 3] == 0.0) continue;
        if (t->cooldown > 1e-6) continue;
        mx = t->x + cos(t->turret) * s->muzzle_offset;
        my = t->y + sin(t->turret) * s->muzzle_offset;
        if (nt_opaque_px(&s->arena, mx, my) || nt_blocked_px(&s->arena, mx, my))
            continue;
        nt_spawn_bullet(s, t);
    }

    /* 4. полёт снарядов */
    n = 0;
    for (j = 0; j < s->nbullets; j++) {
        nt_bullet b = s->bullets[j];
        nt_step_bullet(s, &b);
        if (b.alive) s->bullets[n++] = b;
    }
    s->nbullets = n;

    /* 5. гибель */
    for (i = 0; i < 2; i++) {
        nt_tank *t = &s->tanks[i];
        if (t->alive && t->hp <= 0.0) t->alive = 0;
    }

    /* 6. итог */
    nt_finish(s);

    /* 7. видимость и наблюдение для следующего тика */
    nt_update_vision(s);
    if (obs_out) nt_fill_obs(s, obs_out);
    (void)dt;
    return s->over;
}

long sim_ticks(void *handle)
{
    return ((nt_sim *)handle)->tick;
}

int sim_over(void *handle)
{
    return ((nt_sim *)handle)->over;
}

/*: Статистика: [shots, hits, ric, ric_taken, rams, dealt, taken, hp] × 2. */
void sim_stats(void *handle, double *out)
{
    nt_sim *s = (nt_sim *)handle;
    int i;
    for (i = 0; i < 2; i++) {
        nt_tank *t = &s->tanks[i];
        out[i * 8 + 0] = (double)t->shots;
        out[i * 8 + 1] = (double)t->hits;
        out[i * 8 + 2] = (double)t->ricochets;
        out[i * 8 + 3] = (double)t->ricochets_taken;
        out[i * 8 + 4] = (double)t->rams;
        out[i * 8 + 5] = t->damage_dealt;
        out[i * 8 + 6] = t->damage_taken;
        out[i * 8 + 7] = t->hp;
    }
}

/*: Полный дамп состояния — для сверки с Python-движком (tools/parity.py). */
int sim_state(void *handle, double *out)
{
    nt_sim *s = (nt_sim *)handle;
    int i, j, k = 0;

    out[k++] = (double)s->tick;
    out[k++] = (double)s->over;
    out[k++] = (double)s->outcome;
    out[k++] = (double)s->end_reason;
    out[k++] = (double)s->nbullets;
    for (i = 0; i < 2; i++) {
        nt_tank *t = &s->tanks[i];
        out[k++] = t->x; out[k++] = t->y; out[k++] = t->hull; out[k++] = t->turret;
        out[k++] = t->vx; out[k++] = t->vy; out[k++] = t->hp;
        out[k++] = t->cooldown; out[k++] = t->ram_cd; out[k++] = (double)t->alive;
        out[k++] = (double)t->sees_enemy;
        for (j = 0; j < 7; j++) out[k++] = t->sight[j];
        out[k++] = (double)t->shots; out[k++] = (double)t->hits;
        out[k++] = (double)t->ricochets; out[k++] = (double)t->ricochets_taken;
        out[k++] = (double)t->rams; out[k++] = t->damage_dealt;
        out[k++] = t->damage_taken;
    }
    for (j = 0; j < s->nbullets; j++) {
        nt_bullet *b = &s->bullets[j];
        if (k + 8 >= NT_STATE_N) break;      /* страховка от переполнения */
        out[k++] = (double)b->owner;
        out[k++] = b->x; out[k++] = b->y; out[k++] = b->dx; out[k++] = b->dy;
        out[k++] = b->life; out[k++] = b->speed; out[k++] = (double)b->skip;
    }
    for (i = 0; i < 624; i++) out[k++] = (double)s->rng.mt[i];
    out[k++] = (double)s->rng.mti;
    return k;
}

void sim_destroy(void *handle)
{
    nt_sim *s = (nt_sim *)handle;
    if (!s) return;
    nt_free_arena(&s->arena);
    free(s);
}

/*: Версия ABI — Python сверяет, чтобы не подхватить старую .so. */
int sim_version(void) { return 3; }
int sim_obs_size(void) { return NT_OBS_N; }
int sim_state_size(void) { return NT_STATE_N; }
int sim_param_count(void) { return P_COUNT; }

/*: Отладочные обёртки примитивов (проверяются в tests/test_native.py). */
double nt_round_raw(double x, int n) { return nt_round(x, n); }
double nt_hypot_raw(double x, double y) { return nt_hypot(x, y); }
double nt_random_raw(void *rng_handle) { return nt_rng_random((nt_rng *)rng_handle); }
