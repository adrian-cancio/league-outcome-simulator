use num_cpus;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use rand::Rng;
use rand::SeedableRng;
use rand_chacha::ChaCha8Rng;
use rayon::prelude::*;
use rayon::ThreadPoolBuilder;
use std::cmp::Ordering;
use std::collections::hash_map::Entry;
use std::collections::HashMap;
use std::sync::Mutex;
use std::sync::Once;

#[derive(Debug, Clone)]
struct ProbabilityDistribution {
    cdf: Vec<f64>,
    dim: usize,
}

#[macro_use]
extern crate lazy_static;

const HOME_ADVANTAGE: f64 = 1.25;
const DEFAULT_LAMBDA: f64 = 1.0;
const DEFAULT_RHO: f64 = -0.1;
const MAX_GOALS: usize = 10;

lazy_static! {
    static ref PROBABILITY_CACHE: Mutex<HashMap<(u64, u64, u64), ProbabilityDistribution>> =
        Mutex::new(HashMap::new());
}

static INIT_RAYON: Once = Once::new();

#[derive(Debug, Clone)]
struct FixtureSimulation {
    home_idx: usize,
    away_idx: usize,
    distribution: ProbabilityDistribution,
}

#[derive(Debug, Clone)]
struct SimulationInput {
    teams: Vec<String>,
    initial_stats: Vec<[i64; 4]>,
    fixtures: Vec<FixtureSimulation>,
}

#[derive(Clone)]
struct SeasonResult {
    order: Vec<usize>,
    final_stats: Vec<[i64; 4]>,
}

struct DixonColes {}

impl DixonColes {
    fn correction_factor(x: i64, y: i64, lambda_x: f64, lambda_y: f64, rho: f64) -> f64 {
        match (x, y) {
            (0, 0) => 1.0 - lambda_x * lambda_y * rho,
            (0, 1) => 1.0 + lambda_x * rho,
            (1, 0) => 1.0 + lambda_y * rho,
            (1, 1) => 1.0 - rho,
            _ => 1.0,
        }
    }

    fn poisson_pmf(k: i64, lambda: f64) -> f64 {
        if lambda <= 0.0 || k < 0 {
            return 0.0;
        }
        let k_float = k as f64;
        let log_lambda = lambda.ln();
        let log_k_factorial = (1..=k).map(|i| (i as f64).ln()).sum::<f64>();
        (-lambda + k_float * log_lambda - log_k_factorial).exp()
    }

    fn result_probability(x: i64, y: i64, lambda_x: f64, lambda_y: f64, rho: f64) -> f64 {
        let p_x = Self::poisson_pmf(x, lambda_x);
        let p_y = Self::poisson_pmf(y, lambda_y);
        let tau = Self::correction_factor(x, y, lambda_x, lambda_y, rho);
        p_x * p_y * tau
    }

    fn precompute_probability_matrix(
        lambda_h: f64,
        lambda_a: f64,
        rho: f64,
        max_goals: usize,
    ) -> ProbabilityDistribution {
        let mut flat_probs = Vec::with_capacity((max_goals + 1) * (max_goals + 1));
        let mut total = 0.0;
        for h in 0..=max_goals {
            for a in 0..=max_goals {
                let p = Self::result_probability(h as i64, a as i64, lambda_h, lambda_a, rho);
                flat_probs.push(p);
                total += p;
            }
        }

        let mut cdf = Vec::with_capacity(flat_probs.len());
        let mut acc = 0.0;
        for prob in flat_probs.iter_mut() {
            *prob /= total.max(f64::EPSILON);
            acc += *prob;
            cdf.push(acc);
        }
        if let Some(last) = cdf.last_mut() {
            *last = 1.0;
        }
        ProbabilityDistribution {
            cdf,
            dim: max_goals + 1,
        }
    }

    fn get_probability_matrix(
        lambda_h: f64,
        lambda_a: f64,
        rho: f64,
        max_goals: usize,
    ) -> ProbabilityDistribution {
        let key = (lambda_h.to_bits(), lambda_a.to_bits(), rho.to_bits());
        let mut cache = PROBABILITY_CACHE
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        match cache.entry(key) {
            Entry::Occupied(entry) => entry.get().clone(),
            Entry::Vacant(entry) => entry
                .insert(Self::precompute_probability_matrix(
                    lambda_h, lambda_a, rho, max_goals,
                ))
                .clone(),
        }
    }

    fn simulate_from_distribution<R: Rng>(rng: &mut R, pd: &ProbabilityDistribution) -> (i64, i64) {
        let u: f64 = rng.gen();
        let idx = match pd
            .cdf
            .binary_search_by(|value| value.partial_cmp(&u).unwrap_or(Ordering::Greater))
        {
            Ok(index) | Err(index) => index.min(pd.cdf.len().saturating_sub(1)),
        };
        ((idx / pd.dim) as i64, (idx % pd.dim) as i64)
    }
}

fn extract_row_stat(row_list: &PyList, index: usize, name: &str) -> PyResult<i64> {
    row_list
        .get_item(index)
        .map_err(|_| PyValueError::new_err(format!("Missing {name} at column {index}")))?
        .extract()
}

fn extract_team_name(row_list: &PyList) -> PyResult<String> {
    row_list
        .get_item(0)
        .map_err(|_| PyValueError::new_err("Missing team name"))?
        .extract()
}

fn parse_simulation_input(
    py: Python,
    base_table: PyObject,
    fixtures: PyObject,
    home_table: PyObject,
    away_table: PyObject,
) -> PyResult<SimulationInput> {
    let base: &PyList = base_table.extract(py)?;
    let fixtures_list: &PyList = fixtures.extract(py)?;
    let home_list: &PyList = home_table.extract(py)?;
    let away_list: &PyList = away_table.extract(py)?;

    let mut teams: Vec<String> = Vec::new();
    let mut initial_stats: Vec<[i64; 4]> = Vec::new();
    let mut team_to_idx: HashMap<String, usize> = HashMap::new();

    for row in base.iter().skip(1) {
        let row_list: &PyList = row.extract()?;
        let team = extract_team_name(row_list)?;
        let m = extract_row_stat(row_list, 1, "matches")?;
        let gf = extract_row_stat(row_list, 5, "goals for")?;
        let ga = extract_row_stat(row_list, 6, "goals against")?;
        let pts = extract_row_stat(row_list, 7, "points")?;
        let index = teams.len();
        teams.push(team.clone());
        team_to_idx.insert(team, index);
        initial_stats.push([pts, gf, ga, m]);
    }

    let num_teams = teams.len();
    let mut home_stats: Vec<(i64, i64, i64)> = vec![(0, 0, 0); num_teams];
    let mut away_stats: Vec<(i64, i64, i64)> = vec![(0, 0, 0); num_teams];

    for row in home_list.iter().skip(1) {
        let row_list: &PyList = row.extract()?;
        let team = extract_team_name(row_list)?;
        let idx = *team_to_idx.get(&team).ok_or_else(|| {
            PyValueError::new_err(format!(
                "Team {team} found in home table but not base table"
            ))
        })?;
        home_stats[idx] = (
            extract_row_stat(row_list, 5, "home goals for")?,
            extract_row_stat(row_list, 6, "home goals against")?,
            extract_row_stat(row_list, 1, "home matches")?,
        );
    }

    for row in away_list.iter().skip(1) {
        let row_list: &PyList = row.extract()?;
        let team = extract_team_name(row_list)?;
        let idx = *team_to_idx.get(&team).ok_or_else(|| {
            PyValueError::new_err(format!(
                "Team {team} found in away table but not base table"
            ))
        })?;
        away_stats[idx] = (
            extract_row_stat(row_list, 5, "away goals for")?,
            extract_row_stat(row_list, 6, "away goals against")?,
            extract_row_stat(row_list, 1, "away matches")?,
        );
    }

    let total_gf: i64 = initial_stats.iter().map(|stats| stats[1]).sum();
    let total_matches: i64 = initial_stats.iter().map(|stats| stats[3]).sum();
    let avg_league_goals = if total_matches > 0 {
        total_gf as f64 / total_matches as f64
    } else {
        DEFAULT_LAMBDA
    };

    let home_total_gf: i64 = home_stats.iter().map(|(gf, _, _)| gf).sum();
    let away_total_gf: i64 = away_stats.iter().map(|(gf, _, _)| gf).sum();
    let home_advantage = if away_total_gf > 0 {
        (home_total_gf as f64 / away_total_gf as f64).clamp(1.0, 1.5)
    } else {
        HOME_ADVANTAGE
    };

    let mut home_attack = vec![1.0; num_teams];
    let mut home_defense = vec![1.0; num_teams];
    let mut away_attack = vec![1.0; num_teams];
    let mut away_defense = vec![1.0; num_teams];

    for idx in 0..num_teams {
        let (hgf, hga, hm) = home_stats[idx];
        if hm > 0 && avg_league_goals > 0.0 {
            home_attack[idx] = (hgf as f64 / hm as f64) / avg_league_goals;
            home_defense[idx] = (hga as f64 / hm as f64) / avg_league_goals;
        }
        let (agf, aga, am) = away_stats[idx];
        if am > 0 && avg_league_goals > 0.0 {
            away_attack[idx] = (agf as f64 / am as f64) / avg_league_goals;
            away_defense[idx] = (aga as f64 / am as f64) / avg_league_goals;
        }
    }

    let mut fixtures: Vec<FixtureSimulation> = Vec::new();
    for item in fixtures_list.iter() {
        let dict: &PyDict = item.extract()?;
        let home_obj = dict
            .get_item("h")
            .ok_or_else(|| PyValueError::new_err("Fixture missing 'h' object"))?;
        let away_obj = dict
            .get_item("a")
            .ok_or_else(|| PyValueError::new_err("Fixture missing 'a' object"))?;
        let home_dict: &PyDict = home_obj
            .downcast()
            .map_err(|_| PyValueError::new_err("Fixture 'h' is not a dict"))?;
        let away_dict: &PyDict = away_obj
            .downcast()
            .map_err(|_| PyValueError::new_err("Fixture 'a' is not a dict"))?;
        let home_name: String = home_dict
            .get_item("title")
            .ok_or_else(|| PyValueError::new_err("Fixture home object missing title"))?
            .extract()?;
        let away_name: String = away_dict
            .get_item("title")
            .ok_or_else(|| PyValueError::new_err("Fixture away object missing title"))?
            .extract()?;

        let home_idx = *team_to_idx.get(&home_name).ok_or_else(|| {
            PyValueError::new_err(format!("Team {home_name} not found in standings"))
        })?;
        let away_idx = *team_to_idx.get(&away_name).ok_or_else(|| {
            PyValueError::new_err(format!("Team {away_name} not found in standings"))
        })?;

        let lambda_h =
            avg_league_goals * home_attack[home_idx] * away_defense[away_idx] * home_advantage;
        let lambda_a = avg_league_goals * away_attack[away_idx] * home_defense[home_idx];
        let distribution =
            DixonColes::get_probability_matrix(lambda_h, lambda_a, DEFAULT_RHO, MAX_GOALS);
        fixtures.push(FixtureSimulation {
            home_idx,
            away_idx,
            distribution,
        });
    }

    Ok(SimulationInput {
        teams,
        initial_stats,
        fixtures,
    })
}

fn simulate_single_season<R: Rng>(input: &SimulationInput, rng: &mut R) -> SeasonResult {
    let num_teams = input.teams.len();
    let mut standings = input.initial_stats.clone();

    for fixture in &input.fixtures {
        let home_idx = fixture.home_idx;
        let away_idx = fixture.away_idx;
        let (gh, ga) = DixonColes::simulate_from_distribution(rng, &fixture.distribution);

        standings[home_idx][1] += gh;
        standings[home_idx][2] += ga;
        standings[home_idx][3] += 1;
        if gh > ga {
            standings[home_idx][0] += 3;
        } else if gh == ga {
            standings[home_idx][0] += 1;
        }

        standings[away_idx][1] += ga;
        standings[away_idx][2] += gh;
        standings[away_idx][3] += 1;
        if ga > gh {
            standings[away_idx][0] += 3;
        } else if gh == ga {
            standings[away_idx][0] += 1;
        }
    }

    let mut order: Vec<usize> = (0..num_teams).collect();
    order.sort_by(|&left, &right| {
        let a = &standings[left];
        let b = &standings[right];
        b[0].cmp(&a[0])
            .then((b[1] - b[2]).cmp(&(a[1] - a[2])))
            .then(b[1].cmp(&a[1]))
            .then(input.teams[left].cmp(&input.teams[right]))
    });

    SeasonResult {
        order,
        final_stats: standings,
    }
}

#[pyfunction]
fn simulate_season(
    py: Python,
    base_table: PyObject,
    fixtures: PyObject,
    home_table: PyObject,
    away_table: PyObject,
    seed: Option<u64>,
) -> PyResult<PyObject> {
    let input = parse_simulation_input(py, base_table, fixtures, home_table, away_table)?;
    let mut rng = match seed {
        Some(value) => ChaCha8Rng::seed_from_u64(value),
        None => ChaCha8Rng::from_entropy(),
    };
    let result = simulate_single_season(&input, &mut rng);

    let standings = PyList::empty(py);
    for &team_idx in &result.order {
        let stats = &result.final_stats[team_idx];
        let team = &input.teams[team_idx];
        let dict = PyDict::new(py);
        dict.set_item("PTS", stats[0])?;
        dict.set_item("GF", stats[1])?;
        dict.set_item("GA", stats[2])?;
        dict.set_item("M", stats[3])?;
        standings.append((team, dict))?;
    }
    Ok(standings.into())
}

#[pyfunction]
fn simulate_bulk(
    py: Python,
    base_table: PyObject,
    fixtures: PyObject,
    home_table: PyObject,
    away_table: PyObject,
    n_sims: usize,
    seed: Option<u64>,
    top_k_tables: usize,
) -> PyResult<PyObject> {
    let input = parse_simulation_input(py, base_table, fixtures, home_table, away_table)?;
    let num_teams = input.teams.len();
    let base_seed = seed.unwrap_or(42);

    let (counts, tables) = (0..n_sims)
        .into_par_iter()
        .map(|sim_index| {
            let mut rng = ChaCha8Rng::seed_from_u64(base_seed.wrapping_add(sim_index as u64));
            let result = simulate_single_season(&input, &mut rng);
            let table_key: Vec<usize> = result.order.clone();
            (result.order, table_key)
        })
        .fold(
            || {
                (
                    vec![vec![0u64; num_teams]; num_teams],
                    HashMap::<Vec<usize>, u64>::new(),
                )
            },
            |(mut acc_counts, mut acc_tables), (order, table_key)| {
                for (pos_idx, &team_idx) in order.iter().enumerate() {
                    acc_counts[team_idx][pos_idx] += 1;
                }
                *acc_tables.entry(table_key).or_insert(0) += 1;
                if top_k_tables > 0 && acc_tables.len() > top_k_tables * 4 {
                    let mut tables: Vec<(Vec<usize>, u64)> = acc_tables.into_iter().collect();
                    tables.sort_by(|a, b| b.1.cmp(&a.1));
                    tables.truncate(top_k_tables * 2);
                    acc_tables = tables.into_iter().collect();
                }
                (acc_counts, acc_tables)
            },
        )
        .reduce(
            || {
                (
                    vec![vec![0u64; num_teams]; num_teams],
                    HashMap::<Vec<usize>, u64>::new(),
                )
            },
            |(mut left_counts, mut left_tables), (right_counts, right_tables)| {
                for team_idx in 0..num_teams {
                    for pos_idx in 0..num_teams {
                        left_counts[team_idx][pos_idx] += right_counts[team_idx][pos_idx];
                    }
                }
                for (table, count) in right_tables {
                    *left_tables.entry(table).or_insert(0) += count;
                }
                if top_k_tables > 0 && left_tables.len() > top_k_tables * 4 {
                    let mut tables: Vec<(Vec<usize>, u64)> = left_tables.into_iter().collect();
                    tables.sort_by(|a, b| b.1.cmp(&a.1));
                    tables.truncate(top_k_tables * 2);
                    left_tables = tables.into_iter().collect();
                }
                (left_counts, left_tables)
            },
        );

    let result = PyDict::new(py);
    let position_counts = PyDict::new(py);
    for (team_idx, team_name) in input.teams.iter().enumerate() {
        let inner = PyDict::new(py);
        for (pos_idx, &count) in counts[team_idx].iter().enumerate() {
            inner.set_item(pos_idx + 1, count)?;
        }
        position_counts.set_item(team_name, inner)?;
    }

    let mut top_tables: Vec<(Vec<usize>, u64)> = tables.into_iter().collect();
    top_tables.sort_by(|a, b| b.1.cmp(&a.1));
    if top_k_tables > 0 {
        top_tables.truncate(top_k_tables);
    }
    let top_tables_py = PyList::empty(py);
    for (table, count) in top_tables {
        let entry = PyDict::new(py);
        let ordered_names: Vec<&String> = table.iter().map(|&idx| &input.teams[idx]).collect();
        entry.set_item("table", ordered_names)?;
        entry.set_item("count", count)?;
        top_tables_py.append(entry)?;
    }

    result.set_item("position_counts", position_counts)?;
    result.set_item("top_tables", top_tables_py)?;
    Ok(result.into())
}

#[pymodule]
fn league_outcome_simulator_rust(_py: Python, m: &PyModule) -> PyResult<()> {
    INIT_RAYON.call_once(|| {
        let _ = ThreadPoolBuilder::new()
            .num_threads(num_cpus::get())
            .build_global();
    });

    m.add_function(wrap_pyfunction!(simulate_season, m)?)?;
    m.add_function(wrap_pyfunction!(simulate_bulk, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dixon_coles_match_returns_reasonable_scores() {
        let mut rng = ChaCha8Rng::seed_from_u64(123);
        let pd = DixonColes::get_probability_matrix(1.4, 0.9, DEFAULT_RHO, MAX_GOALS);
        let (home, away) = DixonColes::simulate_from_distribution(&mut rng, &pd);
        assert!(home >= 0);
        assert!(away >= 0);
        assert!(home <= MAX_GOALS as i64);
        assert!(away <= MAX_GOALS as i64);
    }
}
