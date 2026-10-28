"""Frozen opt-in venue model and bounded-window contracts."""
import math

import pytest

from league_outcome_simulator.evaluation import evaluate
from league_outcome_simulator.simulation import get_rust_module
from tests.test_evaluation import ORIGIN, event


def test_actual_parse_path_representative_rates_and_default():
    rust = get_rust_module(auto_build=False)
    header = ['Team', 'P', 'W', 'D', 'L', 'GF', 'GA', 'Pts']
    base = [header, ['A', 4, 0, 0, 0, 5, 5, 0],
            ['B', 4, 0, 0, 0, 5, 5, 0]]
    home = [header, ['A', 2, 0, 0, 0, 3, 2, 0],
            ['B', 2, 0, 0, 0, 3, 2, 0]]
    away = [header, ['A', 2, 0, 0, 0, 2, 3, 0],
            ['B', 2, 0, 0, 0, 2, 3, 0]]
    args = (base, [{'h': {'title': 'A'}, 'a': {'title': 'B'}}], home, away)
    assert rust.predict_outcomes(*args) == rust.predict_outcomes(*args, 'current_v1')
    actual = rust.predict_outcomes(*args, 'venue_normalized_v1')[0]
    # Independent finite Poisson/DC enumeration: parsed H=1.5, A=1.
    _, _, rho, maximum = rust.MODEL_CONSTANTS
    masses = [0., 0., 0.]
    for h in range(int(maximum) + 1):
        for a in range(int(maximum) + 1):
            tau = {(0, 0): 1-1.5*rho, (0, 1): 1+1.5*rho,
                   (1, 0): 1+rho, (1, 1): 1-rho}.get((h, a), 1)
            p = math.exp(-2.5)*1.5**h/math.factorial(h)/math.factorial(a)*tau
            masses[0 if h > a else 1 if h == a else 2] += p
    assert actual == pytest.approx([p/sum(masses) for p in masses])
    with pytest.raises(ValueError, match='Unknown model'):
        rust.predict_outcomes(*args, 'unknown')
    home[1][1] = 0
    with pytest.raises(ValueError, match='venue samples'):
        rust.predict_outcomes(*args, 'venue_normalized_v1')
    home[1][1] = 2
    home[1][5] = home[2][5] = 0
    with pytest.raises(ValueError, match='positive venue means'):
        rust.predict_outcomes(*args, 'venue_normalized_v1')


def test_bounded_windows_candidate_report():
    events = [event(1, -86400, (3, 2)), event(2, 1), event(3, 86400)]
    report = evaluate(events, ORIGIN, '2024-01-02T00:00:00Z', True)
    assert report['test_ids'] == [2]
    assert report['model_ids']['candidate'] == 'venue_normalized_v1'
    assert set(report['metrics']) == {'model', 'candidate', 'uniform', 'empirical'}
    assert report['invalid_probability_count'] == 0
    with pytest.raises(ValueError, match='strictly after'):
        evaluate(events, ORIGIN, ORIGIN)
