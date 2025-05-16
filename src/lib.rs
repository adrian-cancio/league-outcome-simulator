use pyo3::prelude::*;
use pyo3::types::{PyList, PyDict};
use std::collections::HashMap;
use rand::thread_rng;
use rand_distr::{Poisson, Distribution};
use rayon::prelude::*;
use rand_chacha::ChaCha8Rng;
use rand::SeedableRng;

#[pyfunction]
fn simulate_season(py: Python, base_table: PyObject, fixtures: PyObject) -> PyResult<PyObject> {
    // Parse Python lists
    let base: &PyList = base_table.extract(py)?;
    let fixtures_list: &PyList = fixtures.extract(py)?;

    // Team stats struct
    struct Stats { pts: i64, gf: i64, ga: i64, m: i64 }
    let mut standings: HashMap<String, Stats> = HashMap::new();

    // Initialize standings from base_table (skip header)
    for row in base.iter().skip(1) {
        let row_list: &PyList = row.extract()?;
        let team: String = row_list.get_item(0)? .extract()?;
        let m: i64 = row_list.get_item(1)? .extract()?;
        let gf: i64 = row_list.get_item(5)? .extract()?;
        let ga: i64 = row_list.get_item(6)? .extract()?;
        let pts: i64 = row_list.get_item(7)? .extract()?;
        standings.insert(team, Stats { pts, gf, ga, m });
    }

    let mut rng = thread_rng();
    // Simulate each fixture
    for match_obj in fixtures_list.iter() {
        let dict: &PyDict = match_obj.extract()?;
        let h: &PyDict = dict.get_item("h").unwrap().downcast()?;
        let a: &PyDict = dict.get_item("a").unwrap().downcast()?;
        let h_team: String = h.get_item("title").unwrap().extract()?;
        let a_team: String = a.get_item("title").unwrap().extract()?;

        // Compute lambdas
        let sh = standings.get(&h_team).unwrap();
        let sa = standings.get(&a_team).unwrap();
        let lambda_h = (sh.gf as f64 / sh.m as f64) * 1.25;
        let lambda_a = sa.gf as f64 / sa.m as f64;

        // Sample goals
        let gh = Poisson::new(lambda_h).unwrap().sample(&mut rng) as i64;
        let ga = Poisson::new(lambda_a).unwrap().sample(&mut rng) as i64;

        // Update standings
        {
            let sh = standings.get_mut(&h_team).unwrap();
            sh.gf += gh;
            sh.ga += ga;
            sh.m += 1;
        }
        {
            let sa = standings.get_mut(&a_team).unwrap();
            sa.gf += ga;
            sa.ga += gh;
            sa.m += 1;
        }
        if gh > ga {
            let sh = standings.get_mut(&h_team).unwrap();
            sh.pts += 3;
        } else if ga > gh {
            let sa = standings.get_mut(&a_team).unwrap();
            sa.pts += 3;
        } else {
            let sh = standings.get_mut(&h_team).unwrap();
            sh.pts += 1;
            let sa = standings.get_mut(&a_team).unwrap();
            sa.pts += 1;
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
    // Parallel batch simulations
    let counts: HashMap<String, Vec<u64>> = (0..n_sims).into_par_iter()
        .map_init(|| ChaCha8Rng::from_entropy(), |rng, _| {
            // simulate one season
            let mut standings: HashMap<String, (i64,i64,i64,i64)> = initial_stats.clone();
            for (h_team, a_team) in &fixtures_list {
                let (pts_h, gf_h, ga_h, m_h) = standings.get(h_team).cloned().unwrap();
                let (pts_a, gf_a, ga_a, m_a) = standings.get(a_team).cloned().unwrap();
                let lambda_h = (gf_h as f64 / m_h as f64) * 1.25;
                let lambda_a = gf_a as f64 / m_a as f64;
                let gh = Poisson::new(lambda_h).unwrap().sample(rng) as i64;
                let ga = Poisson::new(lambda_a).unwrap().sample(rng) as i64;
                standings.insert(h_team.clone(), (pts_h, gf_h+gh, ga_h+ga, m_h+1));
                standings.insert(a_team.clone(), (pts_a, gf_a+ga, ga_a+gh, m_a+1));
                if gh > ga {
                    standings.get_mut(h_team).unwrap().0 += 3;
                } else if ga > gh {
                    standings.get_mut(a_team).unwrap().0 += 3;
                } else {
                    standings.get_mut(h_team).unwrap().0 += 1;
                    standings.get_mut(a_team).unwrap().0 += 1;
                }
            }
            // determine order
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
    m.add_function(wrap_pyfunction!(simulate_season, m)?)?;
    m.add_function(wrap_pyfunction!(simulate_bulk, m)?)?;
    Ok(())
}