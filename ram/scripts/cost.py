"""Token costing for ram: dollars at API prices and Opus-equivalent tokens (the usage-limit estimate)."""
import json, os, re

_P = json.load(open(os.path.join(os.path.dirname(__file__), '..', 'prices.json')))
_M = _P['models']


def price_for(model):
    """Return (price entry, known). Dated ids like claude-haiku-4-5-20251001 match their base id."""
    if model in _M:
        return _M[model], True
    base = re.sub(r'-\d{8}$', '', model or '')
    if base in _M:
        return _M[base], True
    return _M[_P['unknown_fallback']], False


def _free(model):
    return model in (None, '', '<synthetic>')


def _rates(model, fresh, cw5m, cw1h, cr):
    """Price entry for this request: the long-prompt rate card when the prompt is over its threshold."""
    p, _ = price_for(model)
    long = p.get('long')
    if long and fresh + cw5m + cw1h + cr > long['over']:
        return dict(p, **{k: long[k] for k in ('in', 'out', 'cr')}), long['in'] / p['in']
    return p, 1.0


def dollars(model, fresh, cw5m, cw1h, cr, out):
    if _free(model):
        return 0.0
    p, _ = _rates(model, fresh, cw5m, cw1h, cr)
    return (fresh * p['in'] + cw5m * p['in'] * _P['cache_write_5m_x'] + cw1h * p['in'] * _P['cache_write_1h_x']
            + cr * p['cr'] + out * p['out']) / 1e6


def opus_eq(model, fresh, cw5m, cw1h, cr, out):
    if _free(model):
        return 0.0
    p, scale = _rates(model, fresh, cw5m, cw1h, cr)
    oe = _P['opus_eq']; x = oe['token_x']
    w = p['oe_weight'] * scale if 'oe_weight' in p else oe['tier_weight'][p['tier']]
    return w * (fresh * x['fresh'] + cw5m * x['cw5m'] + cw1h * x['cw1h']
                                           + cr * x['cr'] + out * x['out'])


def tier(model):
    return None if _free(model) else price_for(model)[0]['tier']
