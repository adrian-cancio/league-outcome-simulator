use pyo3::prelude::*;
use pyo3::types::{PyList, PyDict};
use std::collections::HashMap;
use rand::thread_rng;
use rand_distr::{Poisson, Distribution};

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

#[pymodule]
fn rust_sim(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(simulate_season, m)?)?;
    Ok(())
}