#!/usr/bin/env python3
"""Run actual Spark executor checks before starting recommendation work."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knowpipe.recommendations.runtime import create_spark, preflight


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index-root', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    spark = None
    result = {'status': 'failed'}
    try:
        spark = create_spark('knowpipe-deployment-preflight', args.index_root)
        result = getattr(spark, '_knowpipe_preflight', None) or preflight(spark, args.index_root)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        result.update(error_type=type(error).__name__, error_code=str(error)[:160])
        if isinstance(getattr(error, 'report', None), dict):
            result['executor_checks'] = error.report
        raise
    finally:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        if spark:
            spark.stop()


if __name__ == '__main__':
    main()
