import argparse
import json
import os
import runpy
import tempfile


def main():
    parser = argparse.ArgumentParser(description='Run one case from any dataset through pipeline.py')
    parser.add_argument('--model', required=True, help='modelName to run')
    parser.add_argument('--dataset', default='../../Datasets/PAT-RT.json',
                        help='dataset JSON path (default: ../../Datasets/PAT-RT.json)')
    args = parser.parse_args()

    current_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(current_dir, '..', '..'))
    dataset_path = os.path.join(current_dir, args.dataset) if not os.path.isabs(args.dataset) else args.dataset

    if not os.path.exists(dataset_path):
        # try relative to repo root
        dataset_path = os.path.join(repo_root, args.dataset)

    with open(dataset_path, 'r', encoding='utf-8') as f:
        dataset = json.load(f)

    selected = [item for item in dataset if item.get('modelName') == args.model]
    if not selected:
        available = ', '.join(item.get('modelName', '') for item in dataset)
        raise RuntimeError(f"model '{args.model}' not found. Available: {available}")

    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as tmp:
        json.dump(selected, tmp, ensure_ascii=False, indent=2)
        tmp_dataset = tmp.name

    os.environ['PAT_DATASET_PATH'] = tmp_dataset
    os.chdir(current_dir)
    try:
        runpy.run_path(os.path.join(current_dir, 'pipeline.py'), run_name='__main__')
    finally:
        try:
            os.remove(tmp_dataset)
        except OSError:
            pass


if __name__ == '__main__':
    main()
