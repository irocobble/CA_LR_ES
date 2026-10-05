import joblib
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "vad3_model.joblib"
bundle = joblib.load(path)

print(f"Type of loaded object: {type(bundle)}")

if isinstance(bundle, dict):
    print(f"\nKeys: {list(bundle.keys())}\n")
    for k, v in bundle.items():
        print(f"  {k}: {type(v)}", end="")
        if hasattr(v, "shape"):
            print(f"  shape={v.shape}")
        elif isinstance(v, (list, tuple)):
            print(f"  len={len(v)}  first few={v[:5]}")
        else:
            print(f"  value={v}")
else:
    print("Not a dict - it's the raw model object itself.")
    print(dir(bundle))
