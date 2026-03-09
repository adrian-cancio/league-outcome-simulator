use num_cpus;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use rand::thread_rng;
use rand::Rng;
use rand::SeedableRng;
use rand_chacha::ChaCha8Rng;
use rand_distr::{Distribution, Poisson};
use rayon::prelude::*;
use rayon::ThreadPoolBuilder;
use std::collections::HashMap;
use std::sync::Mutex;

/// Cached cumulative distribution for fast sampling
#[derive(Clone)]
struct ProbabilityDistribution {
    cdf: Vec<f64>,
    dim: usize,
}

#[macro_use]
extern crate lazy_static;

// Simulation constants
const HOME_ADVANTAGE: f64 = 1.25;
const DEFAULT_LAMBDA: f64 = 1.0;
const DEFAULT_RHO: f64 = -0.1; // Dixon-Coles correlation parameter

// Global cache for precomputed probability matrices
lazy_static! {
    static ref PROBABILITY_CACHE: Mutex<HashMap<(u64, u64, u64), ProbabilityDistribution>> =
        Mutex::new(HashMap::new());
}

/// DixonColes - A module for football match simulation using the Dixon-Coles model
struct DixonColes {}

impl DixonColes {
    // Dixon-Coles correction factor (tau)
    fn correction_factor(x: i64, y: i64, lambda_x: f64, lambda_y: f64, rho: f64) -> f64 {
        if x == 0 && y == 0 {
            1.0 - lambda_x * lambda_y * rho
        } else if x == 0 && y == 1 {
            1.0 + lambda_x * rho
        } else if x == 1 && y == 0 {
            1.0 + lambda_y * rho
        } else if x == 1 && y == 1 {
            1.0 - rho
        } else {
            1.0
        }
    }

    // Calculate Poisson probability mass function
    fn poisson_pmf(k: i64, lambda: f64) -> f64 {
        if lambda <= 0.0 || k < 0 {
            return 0.0;
        }
        let k_float = k as f64;
        let log_lambda = lambda.ln();
        let log_k_factorial = (1..=k).map(|i| (i as f64).ln()).sum::<f64>();
        (-lambda + k_float * log_lambda - log_k_factorial).exp()
    }

    // Dixon-Coles probability for a result (x, y)
    fn result_probability(x: i64, y: i64, lambda_x: f64, lambda_y: f64, rho: f64) -> f64 {
        let p_x = Self::poisson_pmf(x, lambda_x);
        let p_y = Self::poisson_pmf(y, lambda_y);
        let tau = Self::correction_factor(x, y, lambda_x, lambda_y, rho);
        p_x * p_y * tau
    }

    // Precompute cumulative distribution for given parameters
    fn precompute_probability_matrix(
        lambda_h: f64,
        lambda_a: f64,
        rho: f64,
        max_goals: usize,
    ) -> ProbabilityDistribution {
        // Construct flat probability vector and normalize
        let mut flat_probs = Vec::with_capacity((max_goals + 1) * (max_goals + 1));
        let mut total = 0.0;
        for h in 0..=max_goals {
            for a in 0..=max_goals {
                let p = Self::result_probability(h as i64, a as i64, lambda_h, lambda_a, rho);
                flat_probs.push(p);
                total += p;
            }
        }
        // Normalize and compute cumulative distribution
        let mut cdf = Vec::with_capacity(flat_probs.len());
        let mut acc = 0.0;
        for prob in flat_probs.iter_mut() {
            *prob /= total;
            acc += *prob;
            cdf.push(acc);
        }
        ProbabilityDistribution {
            cdf,
            dim: max_goals + 1,
        }
    }

    // Get or compute cumulative distribution (cached)
    fn get_probability_matrix(
        lambda_h: f64,
        lambda_a: f64,
        rho: f64,
        max_goals: usize,
    ) -> ProbabilityDistribution {
        let key = (lambda_h.to_bits(), lambda_a.to_bits(), rho.to_bits());
        let mut cache = PROBABILITY_CACHE.lock().unwrap();
        let pd = cache.entry(key).or_insert_with(|| {
            Self::precompute_probability_matrix(lambda_h, lambda_a, rho, max_goals)
        });
        pd.clone()
    }

    // Simulate a match using Dixon-Coles model
    fn simulate_match<R: Rng>(
        rng: &mut R,
        lambda_h: f64,
        lambda_a: f64,
        rho: f64,
        max_goals: usize,
    ) -> (i64, i64) {
        // Get or build cached cumulative distribution
        let pd = Self::get_probability_matrix(lambda_h, lambda_a, rho, max_goals);
        // Sample uniform value and binary search in CDF
        let u: f64 = rng.gen();
        let idx = match pd.cdf.binary_search_by(|v| v.partial_cmp(&u).unwrap()) {
            Ok(i) | Err(i) => i,
        };
        let home_goals = (idx / pd.dim) as i64;
        let away_goals = (idx % pd.dim) as i64;

        (home_goals, away_goals)
    }
}

/// FootballSimulation - A module for simulating football seasons
struct FootballSimulation {}

impl FootballSimulation {
    // Simulate a single match with given parameters
    fn simulate_match<R: Rng>(rng: &mut R, lambda_h: f64, lambda_a: f64) -> (i64, i64) {
        // Use Dixon-Coles model when appropriate
        if lambda_h > 0.0 && lambda_a > 0.0 {
            DixonColes::simulate_match(rng, lambda_h, lambda_a, DEFAULT_RHO, 10)
        } else {
            // Fallback to standard Poisson if lambdas are invalid
            let gh = if lambda_h > 0.0 {
                Poisson::new(lambda_h).unwrap().sample(rng) as i64
            } else {
                0
            };
            let ga = if lambda_a > 0.0 {
                Poisson::new(lambda_a).unwrap().sample(rng) as i64
            } else {
                0
            };
            (gh, ga)
        }
    }
}

#[pyfunction]
fn simulate_season(
    py: Python,
    base_table: PyObject,
    fixtures: PyObject,
    home_table: PyObject,
    away_table: PyObject,
) -> PyResult<PyObject> {
    // Parse Python lists
    let base: &PyList = base_table.extract(py)?;
    let fixtures_list: &PyList = fixtures.extract(py)?;
    let home_list: &PyList = home_table.extract(py)?;
    let away_list: &PyList = away_table.extract(py)?;

    // Team stats struct
    #[derive(Debug, Clone)]
    struct Stats {
        pts: i64,
        gf: i64,
        ga: i64,
        m: i64,
    }
    let mut standings: HashMap<String, Stats> = HashMap::new();
    // Home-only and away-only stats: (goals_for, goals_against, matches)
    let mut home_stats: HashMap<String, (i64, i64, i64)> = HashMap::new();
    let mut away_stats: HashMap<String, (i64, i64, i64)> = HashMap::new();

    // Initialize standings from base_table (skip header)
    for row in base.iter().skip(1) {
        let row_list: &PyList = row.extract()?;
        let team: String = row_list.get_item(0)?.extract()?;
        let m: i64 = row_list.get_item(1)?.extract()?;
        let gf: i64 = row_list.get_item(5)?.extract()?;
        let ga: i64 = row_list.get_item(6)?.extract()?;
        let pts: i64 = row_list.get_item(7)?.extract()?;
        standings.insert(team, Stats { pts, gf, ga, m });
    }
    // Initialize home_stats from home_list (skip header)
    for row in home_list.iter().skip(1) {
        let row_list: &PyList = row.extract()?;
        let team: String = row_list.get_item(0)?.extract()?;
        let m: i64 = row_list.get_item(1)?.extract()?;
        let gf: i64 = row_list.get_item(5)?.extract()?;
        let ga: i64 = row_list.get_item(6)?.extract()?;
        home_stats.insert(team, (gf, ga, m));
    }
    // Initialize away_stats from away_list (skip header)
    for row in away_list.iter().skip(1) {
        let row_list: &PyList = row.extract()?;
        let team: String = row_list.get_item(0)?.extract()?;
        let m: i64 = row_list.get_item(1)?.extract()?;
        let gf: i64 = row_list.get_item(5)?.extract()?;
        let ga: i64 = row_list.get_item(6)?.extract()?;
        away_stats.insert(team, (gf, ga, m));
    }

    // Calculate league average goals per team per match
    let total_gf: i64 = standings.values().map(|s| s.gf).sum();
    let total_matches: i64 = standings.values().map(|s| s.m).sum();
    let avg_league_goals = if total_matches > 0 {
        total_gf as f64 / total_matches as f64
    } else {
        DEFAULT_LAMBDA
    };

    // Calculate dynamic home advantage from actual data
    let home_total_gf: i64 = home_stats.values().map(|(gf, _, _)| gf).sum();
    let away_total_gf: i64 = away_stats.values().map(|(gf, _, _)| gf).sum();
    let home_advantage = if away_total_gf > 0 {
        (home_total_gf as f64 / away_total_gf as f64).clamp(1.0, 1.5)
    } else {
        HOME_ADVANTAGE
    };

    let mut rng = thread_rng();
    // Simulate each fixture
    for match_obj in fixtures_list.iter() {
        let dict: &PyDict = match_obj.extract()?;
        // Safely get home and away dicts
        let h_any = dict
            .get_item("h")
            .ok_or_else(|| PyValueError::new_err("Fixture missing h key"))?;
        let h: &PyDict = h_any
            .downcast()
            .map_err(|_| PyValueError::new_err("Fixture h is not a dict"))?;
        let a_any = dict
            .get_item("a")
            .ok_or_else(|| PyValueError::new_err("Fixture missing a key"))?;
        let a: &PyDict = a_any
            .downcast()
            .map_err(|_| PyValueError::new_err("Fixture a is not a dict"))?;

        // Safely get team titles
        let h_team: String = h
            .get_item("title")
            .ok_or_else(|| PyValueError::new_err("Missing title in home object"))?
            .extract()?;
        let a_team: String = a
            .get_item("title")
            .ok_or_else(|| PyValueError::new_err("Missing title in away object"))?
            .extract()?;

        // Verify teams exist in standings
        if !standings.contains_key(&h_team) {
            return Err(PyValueError::new_err(format!(
                "Team {} not found in standings",
                h_team
            )));
        }
        if !standings.contains_key(&a_team) {
            return Err(PyValueError::new_err(format!(
                "Team {} not found in standings",
                a_team
            )));
        }

        // Attack/Defense model: lambda considers both team's attack AND opponent's defense
        // Get home team's attack strength (goals scored at home / league avg)
        let (home_gf, home_ga, home_m) = home_stats.get(&h_team).copied().unwrap_or((0, 0, 0));
        let attack_h = if home_m > 0 && avg_league_goals > 0.0 {
            (home_gf as f64 / home_m as f64) / avg_league_goals
        } else {
            1.0
        };
        // Get home team's defense strength (goals conceded at home / league avg)
        let defense_h = if home_m > 0 && avg_league_goals > 0.0 {
            (home_ga as f64 / home_m as f64) / avg_league_goals
        } else {
            1.0
        };

        // Get away team's attack strength (goals scored away / league avg)
        let (away_gf, away_ga, away_m) = away_stats.get(&a_team).copied().unwrap_or((0, 0, 0));
        let attack_a = if away_m > 0 && avg_league_goals > 0.0 {
            (away_gf as f64 / away_m as f64) / avg_league_goals
        } else {
            1.0
        };
        // Get away team's defense strength (goals conceded away / league avg)
        let defense_a = if away_m > 0 && avg_league_goals > 0.0 {
            (away_ga as f64 / away_m as f64) / avg_league_goals
        } else {
            1.0
        };

        // Calculate lambdas using attack/defense model
        // Home team's expected goals = league_avg * home_attack * away_defense * home_advantage
        // Away team's expected goals = league_avg * away_attack * home_defense
        let lambda_h = avg_league_goals * attack_h * defense_a * home_advantage;
        let lambda_a = avg_league_goals * attack_a * defense_h;

        // Simulate match using appropriate method
        let (gh, ga) = FootballSimulation::simulate_match(&mut rng, lambda_h, lambda_a);

        // Update standings - safely handle home team
        if let Some(sh_mut) = standings.get_mut(&h_team) {
            sh_mut.gf += gh;
            sh_mut.ga += ga;
            sh_mut.m += 1;

            if gh > ga {
                sh_mut.pts += 3;
            } else if gh == ga {
                sh_mut.pts += 1;
            }
        } else {
            return Err(PyValueError::new_err(format!(
                "Team {} not found for update",
                h_team
            )));
        }

        // Update standings - safely handle away team
        if let Some(sa_mut) = standings.get_mut(&a_team) {
            sa_mut.gf += ga;
            sa_mut.ga += gh;
            sa_mut.m += 1;

            if ga > gh {
                sa_mut.pts += 3;
            } else if gh == ga {
                sa_mut.pts += 1;
            }
        } else {
            return Err(PyValueError::new_err(format!(
                "Team {} not found for update",
                a_team
            )));
        }
    }

    // Sort standings
    let mut vec: Vec<(String, Stats)> = standings.into_iter().collect();
    vec.sort_by(|a, b| {
        b.1.pts
            .cmp(&a.1.pts)
            .then((b.1.gf - b.1.ga).cmp(&(a.1.gf - a.1.ga)))
            .then(b.1.gf.cmp(&a.1.gf))
    });

    // Build Python list of (team, dict)
    let result = PyList::empty(py);
    for (team, s) in vec {
        let d = PyDict::new(py);
        d.set_item("PTS", s.pts)?;
        d.set_item("GF", s.gf)?;
        d.set_item("GA", s.ga)?;
        d.set_item("M", s.m)?;
        result.append((team, d))?;
    }
    Ok(result.into())
}

/// Batch simulate many seasons in parallel and return position counts per team
/// OPTIMIZED VERSION: Uses indices instead of strings, precalculates lambdas, uses Vec instead of HashMap
#[pyfunction]
fn simulate_bulk(
    py: Python,
    base_table: PyObject,
    fixtures: PyObject,
    home_table: PyObject,
    away_table: PyObject,
    n_sims: usize,
) -> PyResult<PyObject> {
    // Extract Python lists
    let base: &PyList = base_table.extract(py)?;
    let fixtures_list: &PyList = fixtures.extract(py)?;
    let home_list: &PyList = home_table.extract(py)?;
    let away_list: &PyList = away_table.extract(py)?;

    // Get team names and create index mapping (String -> usize)
    let teams: Vec<String> = base
        .iter()
        .skip(1)
        .map(|row| {
            let row_list: &PyList = row.extract().unwrap();
            row_list.get_item(0).unwrap().extract().unwrap()
        })
        .collect();

    let num_teams = teams.len();
    let team_to_idx: HashMap<String, usize> = teams
        .iter()
        .enumerate()
        .map(|(i, t)| (t.clone(), i))
        .collect();

    // Initial stats as Vec indexed by team index: [pts, gf, ga, m]
    let initial_stats: Vec<[i64; 4]> = base
        .iter()
        .skip(1)
        .map(|row| {
            let row_list: &PyList = row.extract().unwrap();
            let m: i64 = row_list.get_item(1).unwrap().extract().unwrap();
            let gf: i64 = row_list.get_item(5).unwrap().extract().unwrap();
            let ga: i64 = row_list.get_item(6).unwrap().extract().unwrap();
            let pts: i64 = row_list.get_item(7).unwrap().extract().unwrap();
            [pts, gf, ga, m]
        })
        .collect();

    // Parse home_stats: index -> (gf, ga, m)
    let mut home_stats: Vec<(i64, i64, i64)> = vec![(0, 0, 0); num_teams];
    for row in home_list.iter().skip(1) {
        let row_list: &PyList = row.extract()?;
        let team: String = row_list.get_item(0)?.extract()?;
        if let Some(&idx) = team_to_idx.get(&team) {
            let m: i64 = row_list.get_item(1)?.extract()?;
            let gf: i64 = row_list.get_item(5)?.extract()?;
            let ga: i64 = row_list.get_item(6)?.extract()?;
            home_stats[idx] = (gf, ga, m);
        }
    }

    // Parse away_stats: index -> (gf, ga, m)
    let mut away_stats: Vec<(i64, i64, i64)> = vec![(0, 0, 0); num_teams];
    for row in away_list.iter().skip(1) {
        let row_list: &PyList = row.extract()?;
        let team: String = row_list.get_item(0)?.extract()?;
        if let Some(&idx) = team_to_idx.get(&team) {
            let m: i64 = row_list.get_item(1)?.extract()?;
            let gf: i64 = row_list.get_item(5)?.extract()?;
            let ga: i64 = row_list.get_item(6)?.extract()?;
            away_stats[idx] = (gf, ga, m);
        }
    }

    // Calculate league average goals per team per match
    let total_gf: i64 = initial_stats.iter().map(|s| s[1]).sum();
    let total_matches: i64 = initial_stats.iter().map(|s| s[3]).sum();
    let avg_league_goals = if total_matches > 0 {
        total_gf as f64 / total_matches as f64
    } else {
        DEFAULT_LAMBDA
    };

    // Calculate dynamic home advantage from actual data
    let home_total_gf: i64 = home_stats.iter().map(|(gf, _, _)| gf).sum();
    let away_total_gf: i64 = away_stats.iter().map(|(gf, _, _)| gf).sum();
    let home_advantage = if away_total_gf > 0 {
        (home_total_gf as f64 / away_total_gf as f64).clamp(1.0, 1.5)
    } else {
        HOME_ADVANTAGE
    };

    // OPTIMIZATION: Precalculate attack/defense strengths for all teams
    // home_attack[i], home_defense[i] for team i when playing at home
    // away_attack[i], away_defense[i] for team i when playing away
    let mut home_attack: Vec<f64> = vec![1.0; num_teams];
    let mut home_defense: Vec<f64> = vec![1.0; num_teams];
    let mut away_attack: Vec<f64> = vec![1.0; num_teams];
    let mut away_defense: Vec<f64> = vec![1.0; num_teams];

    for i in 0..num_teams {
        let (hgf, hga, hm) = home_stats[i];
        if hm > 0 && avg_league_goals > 0.0 {
            home_attack[i] = (hgf as f64 / hm as f64) / avg_league_goals;
            home_defense[i] = (hga as f64 / hm as f64) / avg_league_goals;
        }
        let (agf, aga, am) = away_stats[i];
        if am > 0 && avg_league_goals > 0.0 {
            away_attack[i] = (agf as f64 / am as f64) / avg_league_goals;
            away_defense[i] = (aga as f64 / am as f64) / avg_league_goals;
        }
    }

    // OPTIMIZATION: Parse fixtures as indices and precalculate lambdas
    // Each fixture: (home_idx, away_idx, lambda_h, lambda_a)
    let fixtures_with_lambdas: Vec<(usize, usize, f64, f64)> = fixtures_list
        .iter()
        .filter_map(|item| {
            let d: &PyDict = item.extract().ok()?;
            let h: &PyDict = d.get_item("h")?.downcast().ok()?;
            let a: &PyDict = d.get_item("a")?.downcast().ok()?;
            let h_name: String = h.get_item("title")?.extract().ok()?;
            let a_name: String = a.get_item("title")?.extract().ok()?;
            let h_idx = *team_to_idx.get(&h_name)?;
            let a_idx = *team_to_idx.get(&a_name)?;

            // Precalculate lambdas for this fixture
            let lambda_h =
                avg_league_goals * home_attack[h_idx] * away_defense[a_idx] * home_advantage;
            let lambda_a = avg_league_goals * away_attack[a_idx] * home_defense[h_idx];

            Some((h_idx, a_idx, lambda_h, lambda_a))
        })
        .collect();

    // OPTIMIZATION: Use simple Poisson sampling directly (no mutex, no cache)
    // This is faster for unique lambda values that rarely repeat
    #[inline(always)]
    fn sample_poisson<R: Rng>(rng: &mut R, lambda: f64) -> i64 {
        if lambda <= 0.0 {
            return 0;
        }
        // For small lambda, use inverse transform
        if lambda < 30.0 {
            let l = (-lambda).exp();
            let mut k: i64 = 0;
            let mut p: f64 = 1.0;
            loop {
                k += 1;
                p *= rng.gen::<f64>();
                if p <= l {
                    return k - 1;
                }
            }
        } else {
            // For large lambda, use normal approximation
            let normal: f64 = rng.gen::<f64>()
                + rng.gen::<f64>()
                + rng.gen::<f64>()
                + rng.gen::<f64>()
                + rng.gen::<f64>()
                + rng.gen::<f64>()
                + rng.gen::<f64>()
                + rng.gen::<f64>()
                + rng.gen::<f64>()
                + rng.gen::<f64>()
                + rng.gen::<f64>()
                + rng.gen::<f64>()
                - 6.0;
            (lambda + normal * lambda.sqrt()).round().max(0.0) as i64
        }
    }

    // Parallel batch simulations
    let counts: Vec<Vec<u64>> = (0..n_sims)
        .into_par_iter()
        .map_init(
            || ChaCha8Rng::from_entropy(),
            |rng, _| {
                // OPTIMIZATION: Use Vec instead of HashMap for standings
                // Each element: [pts, gf, ga, m]
                let mut standings: Vec<[i64; 4]> = initial_stats.clone();

                for &(h_idx, a_idx, lambda_h, lambda_a) in &fixtures_with_lambdas {
                    // Sample goals using fast Poisson
                    let gh = sample_poisson(rng, lambda_h);
                    let ga = sample_poisson(rng, lambda_a);

                    // Update home team stats
                    standings[h_idx][1] += gh; // gf
                    standings[h_idx][2] += ga; // ga
                    standings[h_idx][3] += 1; // m
                    if gh > ga {
                        standings[h_idx][0] += 3; // pts
                    } else if gh == ga {
                        standings[h_idx][0] += 1;
                    }

                    // Update away team stats
                    standings[a_idx][1] += ga; // gf
                    standings[a_idx][2] += gh; // ga
                    standings[a_idx][3] += 1; // m
                    if ga > gh {
                        standings[a_idx][0] += 3; // pts
                    } else if ga == gh {
                        standings[a_idx][0] += 1;
                    }
                }

                // Create indices and sort by standings
                let mut indices: Vec<usize> = (0..num_teams).collect();
                indices.sort_by(|&a, &b| {
                    let sa = &standings[a];
                    let sb = &standings[b];
                    sb[0]
                        .cmp(&sa[0]) // pts desc
                        .then((sb[1] - sb[2]).cmp(&(sa[1] - sa[2]))) // gd desc
                        .then(sb[1].cmp(&sa[1])) // gf desc
                });

                indices
            },
        )
        .fold(
            || vec![vec![0u64; num_teams]; num_teams],
            |mut acc, order| {
                for (pos, &team_idx) in order.iter().enumerate() {
                    acc[team_idx][pos] += 1;
                }
                acc
            },
        )
        .reduce(
            || vec![vec![0u64; num_teams]; num_teams],
            |mut a, b| {
                for i in 0..num_teams {
                    for j in 0..num_teams {
                        a[i][j] += b[i][j];
                    }
                }
                a
            },
        );

    // Build Python dict: team -> dict(position->count)
    let py_dict = PyDict::new(py);
    for (team_idx, team_name) in teams.iter().enumerate() {
        let inner = PyDict::new(py);
        for (pos, &count) in counts[team_idx].iter().enumerate() {
            inner.set_item(pos + 1, count)?;
        }
        py_dict.set_item(team_name, inner)?;
    }
    Ok(py_dict.into())
}

#[pymodule]
fn league_outcome_simulator_rust(_py: Python, m: &PyModule) -> PyResult<()> {
    // Configure Rayon to use all CPU cores available
    ThreadPoolBuilder::new()
        .num_threads(num_cpus::get())
        .build_global()
        .expect("Failed to build global thread pool");

    m.add_function(wrap_pyfunction!(simulate_season, m)?)?;
    m.add_function(wrap_pyfunction!(simulate_bulk, m)?)?;
    Ok(())
}
