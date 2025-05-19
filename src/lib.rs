use pyo3::prelude::*;
use pyo3::types::{PyList, PyDict};
use std::collections::HashMap;
use rand::thread_rng;
use rand_distr::{Poisson, Distribution};
use rayon::prelude::*;
use rayon::ThreadPoolBuilder;
use num_cpus;
use rand_chacha::ChaCha8Rng;
use rand::SeedableRng;
use pyo3::exceptions::PyValueError;
use rand::distributions::WeightedIndex;
use rand::prelude::*;
use std::sync::Mutex;

#[macro_use]
extern crate lazy_static;

const HOME_ADVANTAGE: f64 = 1.25;
const DEFAULT_LAMBDA: f64 = 1.0;
const DEFAULT_RHO: f64 = -0.1;  // Dixon-Coles correlation parameter

// Dixon-Coles correction factor (tau)
fn dixon_coles_tau(x: i64, y: i64, lambda_x: f64, lambda_y: f64, rho: f64) -> f64 {
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
fn dixon_coles_probability(x: i64, y: i64, lambda_x: f64, lambda_y: f64, rho: f64) -> f64 {
    let p_x = poisson_pmf(x, lambda_x);
    let p_y = poisson_pmf(y, lambda_y);
    let tau = dixon_coles_tau(x, y, lambda_x, lambda_y, rho);
    p_x * p_y * tau
}

// Global cache for precomputed probability matrices
lazy_static! {
    static ref PROBABILITY_CACHE: Mutex<HashMap<(u64, u64, u64), Vec<Vec<f64>>>> = Mutex::new(HashMap::new());
}

// Precompute probability matrix for given parameters
fn precompute_probability_matrix(lambda_h: f64, lambda_a: f64, rho: f64, max_goals: usize) -> Vec<Vec<f64>> {
    let mut prob_matrix = vec![vec![0.0; max_goals + 1]; max_goals + 1];
    let mut total_prob = 0.0;

    for h in 0..=max_goals {
        for a in 0..=max_goals {
            let prob = dixon_coles_probability(h as i64, a as i64, lambda_h, lambda_a, rho);
            prob_matrix[h][a] = prob;
            total_prob += prob;
        }
    }

    // Normalize probabilities
    for h in 0..=max_goals {
        for a in 0..=max_goals {
            prob_matrix[h][a] /= total_prob;
        }
    }

    prob_matrix
}

// Get or compute probability matrix
fn get_probability_matrix(lambda_h: f64, lambda_a: f64, rho: f64, max_goals: usize) -> Vec<Vec<f64>> {
    // use to_bits to get u64 for hashing
    let key = (lambda_h.to_bits(), lambda_a.to_bits(), rho.to_bits());
    let mut cache = PROBABILITY_CACHE.lock().unwrap();

    if let Some(matrix) = cache.get(&key) {
        return matrix.clone();
    }

    let matrix = precompute_probability_matrix(lambda_h, lambda_a, rho, max_goals);
    cache.insert(key, matrix.clone());
    matrix
}

// Simulate a match using Dixon-Coles model
fn simulate_dixon_coles_match<R: Rng>(rng: &mut R, lambda_h: f64, lambda_a: f64, rho: f64, max_goals: usize) -> (i64, i64) {
    let prob_matrix = get_probability_matrix(lambda_h, lambda_a, rho, max_goals);

    // Flatten the matrix and create a weighted distribution
    let flat_probs: Vec<f64> = prob_matrix.iter().flat_map(|row| row.iter()).cloned().collect();
    let dist = WeightedIndex::new(&flat_probs).unwrap();

    // Sample from the distribution
    let idx = dist.sample(rng);
    let home_goals = (idx / (max_goals + 1)) as i64;
    let away_goals = (idx % (max_goals + 1)) as i64;

    (home_goals, away_goals)
}

#[pyfunction]
fn simulate_season(py: Python, base_table: PyObject, fixtures: PyObject) -> PyResult<PyObject> {
    // Parse Python lists
    let base: &PyList = base_table.extract(py)?;
    let fixtures_list: &PyList = fixtures.extract(py)?;

    // Team stats struct
    #[derive(Debug, Clone)]
    struct Stats { pts: i64, gf: i64, ga: i64, m: i64 }
    let mut standings: HashMap<String, Stats> = HashMap::new();

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

    let mut rng = thread_rng();
    // Simulate each fixture
    for match_obj in fixtures_list.iter() {
        let dict: &PyDict = match_obj.extract()?;
        // Safely get home and away dicts
        let h_any = dict.get_item("h").ok_or_else(|| PyValueError::new_err("Fixture missing h key"))?;
        let h: &PyDict = h_any.downcast().map_err(|_| PyValueError::new_err("Fixture h is not a dict"))?;
        let a_any = dict.get_item("a").ok_or_else(|| PyValueError::new_err("Fixture missing a key"))?;
        let a: &PyDict = a_any.downcast().map_err(|_| PyValueError::new_err("Fixture a is not a dict"))?;
        
        // Safely get team titles
        let h_team: String = h.get_item("title").ok_or_else(|| PyValueError::new_err("Missing title in home object"))?.extract()?;
        let a_team: String = a.get_item("title").ok_or_else(|| PyValueError::new_err("Missing title in away object"))?.extract()?;

        // Compute lambdas
        let sh = match standings.get(&h_team) {
            Some(stats) => stats.clone(),
            None => return Err(PyValueError::new_err(format!("Team {} not found in standings", h_team))),
        };
        
        let sa = match standings.get(&a_team) {
            Some(stats) => stats.clone(),
            None => return Err(PyValueError::new_err(format!("Team {} not found in standings", a_team))),
        };
        
        let lambda_h = if sh.m > 0 {
            (sh.gf as f64 / sh.m as f64) * HOME_ADVANTAGE
        } else {
            HOME_ADVANTAGE
        };
        
        let lambda_a = if sa.m > 0 {
            sa.gf as f64 / sa.m as f64
        } else {
            DEFAULT_LAMBDA
        };

        // Use Dixon-Coles model (with correlation adjustment tau) when both lambdas are positive
        let (gh, ga) = if lambda_h > 0.0 && lambda_a > 0.0 {
            simulate_dixon_coles_match(&mut rng, lambda_h, lambda_a, DEFAULT_RHO, 10)
        } else {
            // Fallback to standard Poisson if lambdas are invalid
            let gh = if lambda_h > 0.0 { Poisson::new(lambda_h).unwrap().sample(&mut rng) as i64 } else { 0 };
            let ga = if lambda_a > 0.0 { Poisson::new(lambda_a).unwrap().sample(&mut rng) as i64 } else { 0 };
            (gh, ga)
        };

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
            return Err(PyValueError::new_err(format!("Team {} not found for update", h_team)));
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
            return Err(PyValueError::new_err(format!("Team {} not found for update", a_team)));
        }
    }

    // Sort standings
    let mut vec: Vec<(String, Stats)> = standings.into_iter().collect();
    vec.sort_by(|a, b| {
        b.1.pts.cmp(&a.1.pts)
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
#[pyfunction]
fn simulate_bulk(py: Python, base_table: PyObject, fixtures: PyObject, n_sims: usize) -> PyResult<PyObject> {
    // Extract Python lists
    let base: Vec<Vec<String>> = base_table.extract(py)?;
    let fixtures_list: Vec<(String, String)> = {
        let fl: &PyList = fixtures.extract(py)?;
        fl.iter().map(|item| {
            let d: &PyDict = item.extract().unwrap();
            let h: &PyDict = d.get_item("h").unwrap().downcast().unwrap();
            let a: &PyDict = d.get_item("a").unwrap().downcast().unwrap();
            (h.get_item("title").unwrap().extract().unwrap(),
             a.get_item("title").unwrap().extract().unwrap())
        }).collect()
    };
    
    // Get team names and initial stats
    let teams: Vec<String> = base.iter().skip(1).map(|row| row[0].clone()).collect();
    let initial_stats: HashMap<String, (i64,i64,i64,i64)> = base.iter().skip(1)
        .map(|row| {
            let team = row[0].clone();
            let m = row[1].parse().unwrap_or(1);
            let gf = row[5].parse().unwrap_or(0);
            let ga = row[6].parse().unwrap_or(0);
            let pts = row[7].parse().unwrap_or(0);
            (team.clone(), (pts,gf,ga,m))
        }).collect();
    
    // Parallel batch simulations using Dixon-Coles for each match
    let counts: HashMap<String, Vec<u64>> = (0..n_sims).into_par_iter()
        .map_init(|| ChaCha8Rng::from_entropy(), |rng, _| {
            // simulate one season
            let mut standings: HashMap<String, (i64,i64,i64,i64)> = initial_stats.clone();
            for (h_team, a_team) in &fixtures_list {
                if let (Some(&(pts_h, gf_h, ga_h, m_h)), Some(&(pts_a, gf_a, ga_a, m_a))) = 
                    (standings.get(h_team), standings.get(a_team)) {
                    
                    let lambda_h = (gf_h as f64 / m_h as f64) * HOME_ADVANTAGE;
                    let lambda_a = gf_a as f64 / m_a as f64;
                    
                    // Use Dixon-Coles model when appropriate
                    let (gh, ga) = if lambda_h > 0.0 && lambda_a > 0.0 {
                        simulate_dixon_coles_match(rng, lambda_h, lambda_a, DEFAULT_RHO, 10)
                    } else {
                        // Fallback to standard Poisson if lambdas are invalid
                        let gh = if lambda_h > 0.0 { Poisson::new(lambda_h).unwrap().sample(rng) as i64 } else { 0 };
                        let ga = if lambda_a > 0.0 { Poisson::new(lambda_a).unwrap().sample(rng) as i64 } else { 0 };
                        (gh, ga)
                    };
                    
                    // Update stats
                    standings.insert(h_team.clone(), (
                        pts_h + if gh > ga { 3 } else if gh == ga { 1 } else { 0 },
                        gf_h + gh,
                        ga_h + ga,
                        m_h + 1
                    ));
                    
                    standings.insert(a_team.clone(), (
                        pts_a + if ga > gh { 3 } else if gh == ga { 1 } else { 0 },
                        gf_a + ga,
                        ga_a + gh,
                        m_a + 1
                    ));
                }
            }
            
            // Determine final order
            let mut order: Vec<(String, (i64,i64,i64,i64))> = standings.into_iter().collect();
            order.sort_by(|a, b| {
                b.1.0.cmp(&a.1.0)
                .then((b.1.1 - b.1.2).cmp(&(a.1.1 - a.1.2)))
                .then(b.1.1.cmp(&a.1.1))
            });
            
            order.into_iter().map(|x| x.0).collect::<Vec<_>>()
        })
        .fold(HashMap::new, |mut acc, order| {
            for (pos, team) in order.iter().enumerate() {
                let entry = acc.entry(team.clone()).or_insert_with(|| vec![0; teams.len()]);
                entry[pos] += 1;
            }
            acc
        })
        .reduce(HashMap::new, |mut a, b| {
            for (team, vec_b) in b {
                let entry = a.entry(team.clone()).or_insert_with(|| vec![0; teams.len()]);
                for i in 0..vec_b.len() { entry[i] += vec_b[i]; }
            }
            a
        });
    
    // Build Python dict: team -> dict(position->count)
    let py_dict = PyDict::new(py);
    for (team, vec) in counts {
        let inner = PyDict::new(py);
        for (i, count) in vec.into_iter().enumerate() {
            inner.set_item(i+1, count)?;
        }
        py_dict.set_item(team, inner)?;
    }
    Ok(py_dict.into())
}

#[pymodule]
fn rust_sim(_py: Python, m: &PyModule) -> PyResult<()> {
    // Configure Rayon to use all CPU cores available
    ThreadPoolBuilder::new()
        .num_threads(num_cpus::get())
        .build_global()
        .expect("Failed to build global thread pool");

    m.add_function(wrap_pyfunction!(simulate_season, m)?)?;
    m.add_function(wrap_pyfunction!(simulate_bulk, m)?)?;
    Ok(())
}
