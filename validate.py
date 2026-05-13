"""
Quick validation of PBScaler connectivity and initialization.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.Config import Config
from util.KubernetesClient import KubernetesClient
from util.PrometheusClient import PrometheusClient
import time

config = Config()
print("=== Testing Kubernetes Client ===")
k8s = KubernetesClient(config)
try:
    svcs = k8s.get_svcs_without_state()
    print(f"  Stateless services ({len(svcs)}): {svcs}")
except Exception as e:
    print(f"  ERROR getting services: {e}")

try:
    counts = k8s.get_svcs_counts()
    print(f"  Pod counts: {counts}")
except Exception as e:
    print(f"  ERROR getting counts: {e}")

print("\n=== Testing Prometheus Client ===")
prom = PrometheusClient(config)
prom.set_time_range(int(round(time.time())) - 60, int(round(time.time())))

try:
    qps = prom.get_svc_qps()
    print(f"  Service QPS: {qps}")
except Exception as e:
    print(f"  ERROR getting QPS: {e}")

try:
    latency = prom.get_svc_latency()
    print(f"  Service latency: {latency}")
except Exception as e:
    print(f"  ERROR getting latency: {e}")

try:
    call_latency = prom.get_call_latency()
    print(f"  Call latency: {call_latency}")
except Exception as e:
    print(f"  ERROR getting call latency: {e}")

try:
    dg = prom.get_call()
    print(f"  Dependency graph nodes: {list(dg.nodes)}")
    print(f"  Dependency graph edges: {list(dg.edges)}")
except Exception as e:
    print(f"  ERROR getting call graph: {e}")

print("\n=== Testing Model Loading ===")
import joblib
model_path = '/Users/quockhanh/Code/PBScaler/simulation/boutique/RandomForestClassify.model'
try:
    model = joblib.load(model_path)
    print(f"  Model loaded: {type(model).__name__}")
    print(f"  n_estimators: {model.n_estimators}")
except Exception as e:
    print(f"  ERROR loading model: {e}")

print("\n=== Validation Complete ===")
