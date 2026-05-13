"""
Generate synthetic training data for the SLO violation predictor.
Creates a CSV with columns: {svc}&qps, {svc}&count, slo_reward
for the Online Boutique benchmark.
"""
import pandas as pd
import numpy as np
import random
import sys
import os

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.Config import Config
from util.PrometheusClient import PrometheusClient
from util.KubernetesClient import KubernetesClient

# Online Boutique services (stateless only)
svcs = [
    'adservice', 'cartservice', 'checkoutservice', 'currencyservice',
    'emailservice', 'frontend', 'paymentservice', 'productcatalogservice',
    'recommendationservice', 'shippingservice'
]

def get_current_cluster_state():
    """Read current QPS and pod counts from the live cluster."""
    config = Config()
    prom = PrometheusClient(config)
    k8s = KubernetesClient(config)

    # Get current QPS per service
    prom.set_time_range(int(round(__import__('time').time())) - 60,
                        int(round(__import__('time').time())))
    qps_dict = prom.get_svc_qps()

    # Get current pod counts
    counts = k8s.get_svcs_counts()

    return qps_dict, counts


def generate_synthetic_data(n_samples=5000):
    """Generate synthetic training data with realistic QPS and pod count configurations."""
    np.random.seed(42)

    rows = []
    max_pod = 8
    min_pod = 1

    # Try to get real QPS baselines from cluster
    try:
        qps_dict, counts = get_current_cluster_state()
        base_qps = {}
        for svc in svcs:
            key = svc + '&qps'
            base_qps[svc] = qps_dict.get(key, 5.0)
        print(f"Current cluster QPS: {base_qps}")
        print(f"Current pod counts: {counts}")
    except Exception as e:
        print(f"Could not read cluster state: {e}")
        print("Using default QPS values")
        base_qps = {svc: 5.0 for svc in svcs}
        counts = {svc: 1 for svc in svcs}

    for _ in range(n_samples):
        row_dict = {}
        pods = {}

        # Generate a workload scenario
        load_multiplier = np.random.choice([0.2, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0])

        for svc in svcs:
            svc_qps = max(0, base_qps.get(svc, 5.0) * load_multiplier *
                         np.random.uniform(0.5, 2.0))
            svc_pods = np.random.randint(min_pod, max_pod + 1)

            row_dict[svc + '&qps'] = svc_qps
            row_dict[svc + '&count'] = svc_pods
            pods[svc] = svc_pods

        # Heuristic SLO label:
        # For simplicity, assume each pod can handle ~10 QPS without SLO violation
        # Frontend and critical services need more capacity
        slo_satisfied = True
        qps_per_pod_threshold = {
            'frontend': 8.0,   # frontend is most sensitive
            'recommendationservice': 8.0,
            'productcatalogservice': 10.0,
        }

        for svc in svcs:
            threshold = qps_per_pod_threshold.get(svc, 12.0)
            if pods[svc] > 0:
                if row_dict[svc + '&qps'] / pods[svc] > threshold:
                    slo_satisfied = False
                    break

        # Add noise: 5% random label flipping
        if np.random.random() < 0.05:
            slo_satisfied = not slo_satisfied

        row_dict['slo_reward'] = 1 if slo_satisfied else 0
        rows.append(row_dict)

    df = pd.DataFrame(rows)

    # Ensure balanced classes
    pos = df[df['slo_reward'] == 1]
    neg = df[df['slo_reward'] == 0]
    print(f"Class distribution: {len(pos)} satisfied, {len(neg)} violated")

    # Rebalance to roughly 50/50
    min_count = min(len(pos), len(neg))
    if min_count > 0:
        pos = pos.sample(min_count, random_state=42)
        neg = neg.sample(min_count, random_state=42)
        df = pd.concat([pos, neg]).sample(frac=1, random_state=42)

    print(f"Final dataset: {len(df)} samples, {len(pos)} per class")
    return df


if __name__ == '__main__':
    # Create train_data directory
    os.makedirs('../train_data/boutique', exist_ok=True)

    df = generate_synthetic_data(5000)
    output_path = '../train_data/boutique/real_trace_5s_2.0.csv'
    df.to_csv(output_path, index=False)
    print(f"Training data saved to {output_path}")
    print(f"Columns: {list(df.columns)}")
    print(df.head())
