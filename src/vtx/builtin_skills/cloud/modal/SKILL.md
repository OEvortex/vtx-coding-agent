---
name: modal
description: Run Python code in the cloud with Modal serverless containers, GPUs, and autoscaling. Use when deploying ML models, running batch processing jobs, scheduling compute-intensive tasks, serving APIs with GPU acceleration, or scientific computing requiring distributed compute.
register_cmd: true
cmd_info: run code on Modal cloud
category: cloud
---

# Modal

Serverless platform for running Python in the cloud. Execute functions on GPUs, scale to thousands of containers, pay only for compute used. Sign up free at https://modal.com ($30/month credits).

## Setup

```bash
pip install modal
modal token new        # Opens browser for login, stores token in ~/.modal.toml
```

## Core Concepts

### Container Images

Define dependencies with Modal Images:

```python
import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install("torch", "transformers", "numpy")
)
app = modal.App("ml-app", image=image)
```

Patterns:
- Python packages: `.uv_pip_install("pandas", "scikit-learn")`
- System packages: `.apt_install("ffmpeg", "git")`
- Docker base: `modal.Image.from_registry("nvidia/cuda:12.1.0-base")`
- Local code: `.add_local_python_source("my_module")`

### Functions

```python
@app.function()
def process_data(file_path: str):
    import pandas as pd
    return pd.read_csv(file_path).describe()

@app.local_entrypoint()
def main():
    result = process_data.remote("data.csv")
```

Run: `modal run script.py`

### GPUs

```python
@app.function(gpu="H100")
def train():
    import torch
    assert torch.cuda.is_available()
```

Types: `T4`, `L4` (inference), `A10`, `A100`, `A100-80GB`, `L40S` (48GB, best value), `H100`, `H200`, `B200`

Multi-GPU: `gpu="H100:8"` for 8x H100

### Resources

```python
@app.function(cpu=8.0, memory=32768, ephemeral_disk=10240)
def heavy_task():
    pass
```

Defaults: 0.125 CPU, 128 MiB RAM.

### Autoscaling & Parallel Execution

```python
@app.function()
def analyze(sample_id: int):
    return result

@app.local_entrypoint()
def main():
    results = list(analyze.map(range(1000)))  # Parallel across containers
```

Config: `max_containers=100`, `min_containers=2`, `buffer_containers=5`

### Volumes (Persistent Storage)

```python
volume = modal.Volume.from_name("my-data", create_if_missing=True)

@app.function(volumes={"/data": volume})
def save(data):
    with open("/data/results.txt", "w") as f:
        f.write(data)
    volume.commit()
```

### Secrets

```bash
modal secret create my-secret KEY=value API_TOKEN=xyz
```

```python
@app.function(secrets=[modal.Secret.from_name("huggingface")])
def use_secret():
    import os
    token = os.environ["HF_TOKEN"]
```

### Web Endpoints

```python
@app.function()
@modal.web_endpoint(method="POST")
def predict(data: dict):
    return {"prediction": model.predict(data["input"])}
```

Deploy: `modal deploy script.py`

### Scheduled Jobs

```python
@app.function(schedule=modal.Cron("0 2 * * *"))
def daily_backup():
    pass

@app.function(schedule=modal.Period(hours=4))
def refresh_cache():
    pass
```

## Common Patterns

### ML Model Serving

```python
import modal

image = modal.Image.debian_slim().uv_pip_install("torch", "transformers")
app = modal.App("llm-inference", image=image)

@app.cls(gpu="L40S")
class Model:
    @modal.enter()
    def load(self):
        from transformers import pipeline
        self.pipe = pipeline("text-classification", device="cuda")

    @modal.method()
    def predict(self, text: str):
        return self.pipe(text)
```

### Batch Processing

```python
@app.function(cpu=2.0, memory=4096)
def process_file(path: str):
    import pandas as pd
    return pd.read_csv(path).shape[0]

@app.local_entrypoint()
def main():
    for count in process_file.map(["f1.csv", "f2.csv", ...]):
        print(f"Processed {count} rows")
```

### GPU Training

```python
@app.function(gpu="A100:2", timeout=3600)
def train(config: dict):
    import torch
    # Multi-GPU training
```

## CLI Commands

```bash
modal run script.py              # Run function
modal deploy script.py           # Deploy endpoint
modal secret create name K=V     # Create secret
modal app list                   # List deployed apps
modal app hide app-name          # Hide app
modal app destroy app-name       # Destroy app
```

## Best Practices

1. Pin dependencies in `.uv_pip_install()` for reproducible builds
2. L40S for inference, H100/A100 for training
3. Use Volumes for model weights and datasets
4. Set `max_containers`/`min_containers` for autoscaling
5. Import packages inside function body if not available locally
6. Use `.map()` for parallel processing
7. Never hardcode API keys — use Secrets
8. Monitor costs at https://modal.com/docs
