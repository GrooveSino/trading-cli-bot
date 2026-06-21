# Trading CLI Bot

这是 `autotrade` workspace 内的 `tbot` 实现目录。用户入口是 workspace 根目录的 `./tbot` wrapper。

完整使用手册已经迁到 Typst：

- `../../docs/manuals/tbot/tbot-manual-min.typ`
- `../../docs/manuals/tbot/tbot-cheatsheet-summy.typ`

构建 PDF：

```bash
cd /Users/groove/Project/code/crypto/autotrade
tools/docs/build_tbot_manuals.sh
```

## Developer Quick Reference

从 workspace 根目录运行：

```bash
./tbot live btc
./tbot live all
./tbot sim btc doctor
./tbot sim btc test
./tbot sim btc test --yes
```

从本目录运行测试和质量门禁：

```bash
PYTHONPATH=src python3 tests/test_standalone_cli.py
python3 -m compileall -q src tests scripts
python3 scripts/check_quality_limits.py --root . --max-lines 349 --max-dir-files 8 --ext py
python3 /Users/groove/.codex/skills/check-maxline/scripts/check_maxline.py --root . --max-lines 349 --ext py
git diff --check
```

## Safety Defaults

- `live` 是 OKX live 观察和账户读取入口。
- `sim` 是 OKX demo 验证入口。
- 实盘 mutation 仍走 `okx` / `risk` 等高级入口，并要求 dry-run 输出的精确确认短语。
- Binance 私有账户检测已禁用；Binance public 衍生品数据只作为 `global_derivatives` 参考。
