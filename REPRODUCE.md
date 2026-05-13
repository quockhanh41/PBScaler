# PBScaler Reproduction Guide

## Overview

PBScaler is a **bottleneck-aware autoscaling controller** for microservice-based applications running on Kubernetes with Istio. It was published in *IEEE Transactions on Services Computing* (2024).

The control loop runs two periodic checks:

| Check | Interval | What it does |
|-------|----------|--------------|
| **Anomaly detection** | 15s | Detects SLO violations on inter-service call edges, builds an abnormal subgraph weighted by Pearson correlation, runs PageRank to find root-cause bottlenecks, then scales them **up** via GA optimization |
| **Waste detection** | 120s | If no SLO violations, runs a t-test to check if QPS has dropped, then scales **down** bottleneck services |

Scaling decisions use a **genetic algorithm** (Geatpy) whose fitness function is a pre-trained **RandomForest classifier** that predicts whether a given pod-count configuration will violate SLOs. The GA only optimizes replica counts for the identified bottleneck services (top K=2), not all services.

---

## Architecture

```
main.py                          # Entry point — picks controller, runs loop
├── PBScaler.py                  # Core controller: anomaly_detect, waste_detection,
│                                #   root_analysis (PageRank), choose_action (GA)
├── config/Config.py             # Cluster config (SLO, K8s path, Prom URLs, duration)
├── util/
│   ├── KubernetesClient.py      # Talks to K8s API (list pods, patch replicas)
│   ├── PrometheusClient.py      # Queries Prometheus (Istio metrics, CPU, memory, QPS)
│   └── GA.py                    # Genetic algorithm optimizer using Geatpy
├── simulation/
│   └── RandomForestClassify.py  # Trains the SLO-violation predictor (the GA's fitness fn)
├── evaluation/
│   ├── Evaluation.py            # Metric evaluation scripts
│   └── Draw.py                  # Plotting
├── monitor/
│   └── MetricCollect.py         # Post-experiment metric collection to CSV
├── others/                      # Baseline controllers for comparison
│   ├── KHPA.py                  # Kubernetes HPA
│   ├── MicroScaler.py
│   ├── Showar.py
│   ├── NoneController.py
│   └── RandomController.py
└── RL/                          # Experimental GNN-based RL approach (separate, not wired in)
    ├── Simulation.py            # State transition model using GAT
    ├── Environment.py           # RL environment
    └── common/StateModel.py     # GAT-based state prediction model
```

---

## Prerequisites

| Software | Version |
|----------|---------|
| Kubernetes | 1.20.4 |
| Istio | 1.13.4 |
| Python | 3.7+ |
| Prometheus | (accessible via HTTP API) |

- A working Kubernetes cluster with `kubectl` configured
- Istio installed with Prometheus metrics enabled
- Prometheus reachable via HTTP API

---

## Step 1: Deploy a Microservice Benchmark

Choose one of two benchmarks. **Online Boutique** is simpler (10 services).

### Online Boutique

```shell
cd benchmarks/microservices-demo/release/
kubectl apply -f kubernetes-manifests.yaml
```

### Train-Ticket

```shell
cd benchmarks/train-ticket/deployment/kubernetes-manifests/quickstart-k8s
kubectl apply -f quickstart-ts-deployment-part1.yml   # databases
kubectl apply -f quickstart-ts-deployment-part2.yml   # services
kubectl apply -f quickstart-ts-deployment-part3.yml   # UI dashboard
```

Verify everything is running:
```shell
kubectl get pods
```

---

## Step 2: Install Python Dependencies

```shell
pip install -r requirements.txt
```

**Note:** `requirements.txt` contains a typo — `~andas==1.4.3` should be `pandas==1.4.3`. Fix it before installing.

If you plan to use the RL module, also install PyG dependencies:
```shell
pip install torch-scatter torch-sparse torch-cluster torch-spline-conv \
  -f https://data.pyg.org/whl/torch-1.7.0+cpu.html
```

---

## Step 3: Fix Hardcoded Paths

The code contains many hardcoded paths from the original authors' environment. You must update them.

### `config/Config.py`

```python
self.k8s_config = '/home/ubuntu/xsy/config'
# → Change to your kubeconfig path (e.g., ~/.kube/config)

self.k8s_yaml = '/home/ubuntu/xsy/microservices-demo/release/kubernetes-manifests.yaml'
# → Change to the absolute path of your benchmark's K8s YAML

self.prom_range_url = "http://192.168.31.202:32030/api/v1/query_range"
# → Change to your Prometheus query_range endpoint

self.prom_no_range_url = "http://192.168.31.202:32030/api/v1/query"
# → Change to your Prometheus query endpoint

self.SLO = 200           # latency SLO in milliseconds — tune for your setup
self.max_pod = 8          # max pods per microservice
self.min_pod = 1          # min pods per microservice
self.duration = 1*20*60   # experiment duration in seconds (20 min)
```

### `main.py` (line 24)

```python
simulation_model_path = '/home/ubuntu/xsy/experiment/autoscaling/simulation/train_ticket/RandomForestClassify.model'
# → Path to your trained RandomForest model
```

### `PBScaler.py` (line 173)

```python
opter = GA('/home/ubuntu/xsy/experiment/autoscaling/simulation/train_ticket/RandomForestClassify.model', ...)
# → Same path as above (this is separate from the constructor argument — update both)
```

### `simulation/RandomForestClassify.py` (line 15)

```python
svcs = ['adservice','cartservice',...]
# → Update with the actual service names from your deployed benchmark
```

---

## Step 4: Collect Historical Training Data

Before PBScaler can run, you need a trained SLO-violation predictor. This requires historical metrics from your cluster.

1. **Generate load** against the benchmark (use the built-in load generator or a tool like Locust)
2. **Collect metrics** covering a range of traffic patterns and SLO violations/satisfactions
3. The dataset should have columns:
   - Per-service: `{service_name}&qps` (QPS), `{service_name}&count` (pod count)
   - Label: `slo_reward` (1 = SLO satisfied, 0 = violated)
4. Place the CSV at `train_data/boutique/real_trace_5s_2.0.csv` (or update the path in `RandomForestClassify.py`)

The `monitor/MetricCollect.py` module can help collect these metrics from a live cluster — it outputs CSVs for call latency, service latency, resource usage, pod counts, QPS, and success rate.

---

## Step 5: Train the SLO Violation Predictor

```shell
cd simulation
python RandomForestClassify.py
```

This trains a `RandomForestClassifier` and saves:
- The model: `./boutique/RandomForestClassify.model` (joblib format)
- ROC curve data: `train_ticket/rf.pkl`

The model input per service is `[service_index, qps, pod_count]` — concatenated for all services — and it predicts whether the configuration will cause SLO violations.

---

## Step 6: Run PBScaler

```shell
python main.py
```

This starts the control loop. It will:

1. **Every 15 seconds** — `anomaly_detect()`:
   - Query per-edge p90 latency from Prometheus
   - Flag edges exceeding `SLO * (1 + ALPHA/2)`
   - If anomalies exist: build abnormal subgraph, weight edges by Pearson correlation between caller latency and callee resource metrics, run PageRank to rank bottleneck services
   - Scale UP the top-K bottlenecks using GA optimization

2. **Every 120 seconds** — `waste_detection()`:
   - If no SLO violations: run a one-tailed t-test comparing current QPS vs. historical QPS
   - If QPS has significantly dropped: scale DOWN

3. After `duration` seconds: stop the loop, collect metrics to `./output/`

---

## Step 7: Run Baseline Controllers

Edit `main.py` to switch controllers for comparison:

```python
controller = initController('KHPA', config)        # Kubernetes HPA
controller = initController('MicroScaler', config)  # MicroScaler
controller = initController('SHOWAR', config)       # SHOWAR
controller = initController('random', config)       # Random baseline
controller = initController('PBScaler', config)     # PBScaler (default)
```

---

## Key Parameters

| Parameter | Location | Default | Meaning |
|-----------|----------|---------|---------|
| `SLO` | `Config.py` | 200ms | Latency SLO threshold |
| `ALPHA` | `PBScaler.py` | 0.2 | SLO violation headroom factor |
| `BETA` | `PBScaler.py` | 0.9 | QPS drop multiplier for waste detection |
| `K` | `PBScaler.py` | 2 | Top-K bottleneck services to scale |
| `CONF` | `PBScaler.py` | 0.05 | p-value threshold for waste t-test |
| `AB_CHECK_INTERVAL` | `PBScaler.py` | 15s | Anomaly detection period |
| `WASTE_CHECK_INTERVAL` | `PBScaler.py` | 120s | Waste detection period |
| `max_pod` | `Config.py` | 8 | Max replicas per service |
| `min_pod` | `Config.py` | 1 | Min replicas per service |

---

## Limitations & Known Issues

1. **No standalone simulator.** PBScaler requires a live Kubernetes cluster with Istio + Prometheus. There is no offline simulation mode for the full controller.

2. **Hardcoded service lists.** `simulation/RandomForestClassify.py` has hardcoded service names — update them to match your benchmark.

3. **Duplicate model path.** The GA model path is hardcoded in both `PBScaler.py:173` and `main.py:24`. Both must be updated.

4. **RL module is separate.** The `RL/` directory contains an experimental GNN-based RL approach using a Graph Attention Network. It is not wired into the main PBScaler controller.

5. **`requirements.txt` typo.** `~andas==1.4.3` should be `pandas==1.4.3`.

6. **Python 3.7+ required.** The `torch==1.7.0` pin is old — newer Python versions may need a more recent PyTorch.

---

## Citation

```bibtex
@article{xie2024pbscaler,
  title={PBScaler: A Bottleneck-aware Autoscaling Framework for Microservice-based Applications},
  author={Xie, Shuaiyu and Wang, Jian and Li, Bing and Zhang, Zekun and Li, Duantengchuan and Hung, Patrick CK},
  journal={IEEE Transactions on Services Computing},
  year={2024},
  publisher={IEEE}
}
```
