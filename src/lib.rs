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
use std::sync::Mutex;
use rand::Rng;

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
 const DEFAULT_RHO: f64 = -0.1;  // Dixon-Coles correlation parameter

 // Global cache for precomputed probability matrices
 lazy_static! {
     static ref PROBABILITY_CACHE: Mutex<HashMap<(u64, u64, u64), ProbabilityDistribution>> = Mutex::new(HashMap::new());
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
    fn precompute_probability_matrix(lambda_h: f64, lambda_a: f64, rho: f64, max_goals: usize) -> ProbabilityDistribution {
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
         ProbabilityDistribution { cdf, dim: max_goals + 1 }
     }

     // Get or compute cumulative distribution (cached)
     fn get_probability_matrix(lambda_h: f64, lambda_a: f64, rho: f64, max_goals: usize) -> ProbabilityDistribution {
         let key = (lambda_h.to_bits(), lambda_a.to_bits(), rho.to_bits());
         let mut cache = PROBABILITY_CACHE.lock().unwrap();
         let pd = cache.entry(key).or_insert_with(|| Self::precompute_probability_matrix(lambda_h, lambda_a, rho, max_goals));
         pd.clone()
     }

     // Simulate a match using Dixon-Coles model
     fn simulate_match<R: Rng>(rng: &mut R, lambda_h: f64, lambda_a: f64, rho: f64, max_goals: usize) -> (i64, i64) {
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
             let gh = if lambda_h > 0.0 { Poisson::new(lambda_h).unwrap().sample(rng) as i64 } else { 0 };
             let ga = if lambda_a > 0.0 { Poisson::new(lambda_a).unwrap().sample(rng) as i64 } else { 0 };
             (gh, ga)
         }
     }
     
     // Calculate expected goals based on team stats
     fn calculate_lambdas(h_stats: &(i64, i64, i64, i64), a_stats: &(i64, i64, i64, i64)) -> (f64, f64) {
         let lambda_h = if h_stats.3 > 0 {
             (h_stats.1 as f64 / h_stats.3 as f64) * HOME_ADVANTAGE
         } else {
             HOME_ADVANTAGE
         };
         
         let lambda_a = if a_stats.3 > 0 {
             a_stats.1 as f64 / a_stats.3 as f64
         } else {
             DEFAULT_LAMBDA
         };
         
         (lambda_h, lambda_a)
     }
 }

 #[pyfunction]
 fn simulate_season(py: Python, base_table: PyObject, fixtures: PyObject, home_table: PyObject, away_table: PyObject) -> PyResult<PyObject> {
     // Parse Python lists
     let base: &PyList = base_table.extract(py)?;
     let fixtures_list: &PyList = fixtures.extract(py)?;
     let home_list: &PyList = home_table.extract(py)?;
     let away_list: &PyList = away_table.extract(py)?;

     // Team stats struct
     #[derive(Debug, Clone)]
     struct Stats { pts: i64, gf: i64, ga: i64, m: i64 }
     let mut standings: HashMap<String, Stats> = HashMap::new();
     // Home-only and away-only scoring rates
     let mut home_stats: HashMap<String, (i64,i64)> = HashMap::new(); // (goals, matches)
     let mut away_stats: HashMap<String, (i64,i64)> = HashMap::new();

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
         home_stats.insert(team, (gf, m));
     }
     // Initialize away_stats from away_list (skip header)
     for row in away_list.iter().skip(1) {
         let row_list: &PyList = row.extract()?;
         let team: String = row_list.get_item(0)?.extract()?;
         let m: i64 = row_list.get_item(1)?.extract()?;
         let gf: i64 = row_list.get_item(5)?.extract()?;
         away_stats.insert(team, (gf, m));
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
         
         // Compute global scoring rates
         let global_h = if sh.m > 0 { sh.gf as f64 / sh.m as f64 } else { DEFAULT_LAMBDA };
         let global_a = if sa.m > 0 { sa.gf as f64 / sa.m as f64 } else { DEFAULT_LAMBDA };
         // Get venue-specific rates and average with global
         let home_rate = home_stats.get(&h_team)
             .map(|&(gf,m)| if m>0 { gf as f64 / m as f64 } else { global_h })
             .unwrap_or(global_h);
         let away_rate = away_stats.get(&a_team)
             .map(|&(gf,m)| if m>0 { gf as f64 / m as f64 } else { global_a })
             .unwrap_or(global_a);
         let lambda_h = ((global_h + home_rate) / 2.0) * HOME_ADVANTAGE;
         let lambda_a = (global_a + away_rate) / 2.0;

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
                    
                     // Calculate lambdas based on team stats
                     let (lambda_h, lambda_a) = FootballSimulation::calculate_lambdas(
                         &(pts_h, gf_h, ga_h, m_h), 
                         &(pts_a, gf_a, ga_a, m_a)
                     );
                     
                     // Simulate the match
                     let (gh, ga) = FootballSimulation::simulate_match(rng, lambda_h, lambda_a);
                     
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
