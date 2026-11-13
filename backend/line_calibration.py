"""Validate saved black/white references. Pure data logic; no motor/GPIO imports.

A complete configuration is NOT evidence of an autonomous route test and is
never applied to the live service by this module.
"""
from datetime import datetime
import math

CHANNELS = ('X1', 'X2', 'X3', 'X4')
PINS = (15, 18, 16, 11)
MIN_SAMPLES = 15


def bits(values):
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        raise ValueError('Four channel levels are required')
    if any(type(x) is not int or x not in (0, 1) for x in values):
        raise ValueError('Channel levels must be integer 0/1, not booleans')
    return list(values)


def finite(value, minimum=0):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError('Invalid nonnegative numeric sample metadata')
    return value


def validate_reference(record, expected_surface):
    if expected_surface not in ('white_paper', 'black_tape'):
        raise ValueError('Unknown reference surface')
    if not isinstance(record, dict) or record.get('reference') != expected_surface:
        raise ValueError('Incorrect reference surface')
    if record.get('user_confirmed_ready') is not True or record.get('errors') != []:
        raise ValueError('Reference setup was not declared ready or capture had errors')
    host = record.get('host', {})
    if not isinstance(host, dict):
        raise ValueError('Invalid reference host metadata')
    if host.get('is_rdk') is not True or host.get('model') != 'D-Robotics RDK X5 V1.0':
        raise ValueError('Reference is not from the expected RDK X5')
    samples = record.get('samples')
    if not isinstance(samples, list) or len(samples) < MIN_SAMPLES:
        raise ValueError('At least 15 distinct, fresh samples are required')
    previous_mono = previous_count = -1
    first = None
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError('Invalid reference sample metadata')
        values = bits(sample.get('value'))
        if sample.get('physical_pins') != list(PINS):
            raise ValueError('Reference channel pins differ from the existing sampler')
        mono = finite(sample.get('monotonic'))
        count = sample.get('successes')
        if type(count) is not int or count < 0:
            raise ValueError('Invalid line sample sequence')
        if mono <= previous_mono or count <= previous_count:
            raise ValueError('Reference repeats or reorders cached samples')
        if finite(sample.get('age_seconds')) >= 2:
            raise ValueError('Reference includes stale line data')
        stamp = datetime.fromisoformat(sample['sampled_at'])
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError('Reference timestamps must include timezone')
        if first is None:
            first = values
        elif values != first:
            raise ValueError('Reference is unstable; adjust mounting/lighting/material and recapture')
        previous_mono, previous_count = mono, count
    # Recompute from samples; do not trust summary labels in the input JSON.
    return first


def build_calibration(white, black, left_to_right=None):
    white_levels = validate_reference(white, 'white_paper')
    black_levels = validate_reference(black, 'black_tape')
    if any(w == b for w, b in zip(white_levels, black_levels)):
        raise ValueError('At least one probe cannot distinguish black tape from white paper')
    for key in ('hostname', 'model'):
        if white['host'].get(key) != black['host'].get(key):
            raise ValueError('References have different host identities')
    if left_to_right is not None:
        if (not isinstance(left_to_right, (list, tuple)) or len(left_to_right) != 4
                or set(left_to_right) != set(CHANNELS)):
            raise ValueError('Observed left-to-right order must contain X1-X4 exactly once')
        order = list(left_to_right)
    else:
        order = None
    return {
        'schema_version': '1.0', 'channel_order': list(CHANNELS),
        'physical_board_pins': list(PINS), 'white_levels': white_levels,
        'black_levels': black_levels, 'physical_left_to_right_order': order,
        'polarity_verified': True, 'order_verified': order is not None,
        'configuration_complete': order is not None,
        'applied_to_live_service': False, 'autonomous_route_verified': False,
        'scope': 'Saved reference observations under declared mounting, lighting and materials only; not a robustness or accuracy guarantee',
    }


def black_in_physical_order(values, calibration):
    """Normalize a sample to black=True ordered from physical left to right."""
    levels = bits(values)
    if (calibration.get('configuration_complete') is not True
            or calibration.get('polarity_verified') is not True
            or calibration.get('order_verified') is not True):
        raise ValueError('Black/white AND physical order calibration must be complete')
    white, black = bits(calibration.get('white_levels')), bits(calibration.get('black_levels'))
    if any(w == b for w, b in zip(white, black)):
        raise ValueError('Calibration lacks black/white contrast')
    order = calibration.get('physical_left_to_right_order')
    if not isinstance(order, list) or len(order) != 4 or set(order) != set(CHANNELS):
        raise ValueError('Invalid physical channel order')
    return [levels[CHANNELS.index(name)] == black[CHANNELS.index(name)] for name in order]
