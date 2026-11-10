#!/usr/bin/env python3
"""Syntax checks, an unused-code scan and isolated unit tests; does not run the live plugin."""
import ast
import os
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def unused_code():
    """No linter dependency: top-level functions, classes and imports nothing refers to."""
    files = sorted(ROOT.glob('lib/*.py')) + sorted(ROOT.glob('scripts/*.py'))
    trees = {path: ast.parse(path.read_text(), str(path)) for path in files}
    used = {'main'}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
            elif isinstance(node, ast.ImportFrom):
                used.update(alias.name for alias in node.names)
    problems = []
    for path, tree in trees.items():
        local = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name not in used:
                problems.append('%s:%d unused %s' % (path.relative_to(ROOT), node.lineno, node.name))
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    name = (alias.asname or alias.name).split('.')[0]
                    if name not in local:
                        problems.append('%s:%d unused import %s' % (path.relative_to(ROOT), node.lineno, name))
    return problems


subprocess.run(['/bin/bash', '-n', str(ROOT / 'token-monitor.30s.sh')], check=True)
problems = unused_code()
if problems:
    sys.exit('Unused code:\n' + '\n'.join(problems))
with tempfile.TemporaryDirectory(prefix='swiftbar-token-tests-') as scratch:
    private_prefixes = ('OCX_', 'KOMARI_', 'DEEPSEEK_', 'XAI_', 'MINIMAX_', 'CLAUDE_',
                        'OPENAI_', 'ANTHROPIC_', 'MOONSHOT_', 'GOOGLE_', 'KIMI_', 'CODEX_', 'GEMINI_', 'WORKBUDDY_', 'QODER_', 'QODERCN_', 'OPENCODE_', 'DSH_', 'MAGPIE_')
    environment = {k:v for k,v in os.environ.items() if not k.startswith(private_prefixes)}
    isolated_home = Path(scratch) / 'home'
    isolated_home.mkdir()
    environment['HOME'] = str(isolated_home)
    environment['SWIFTBAR_PLUGIN_DATA_PATH'] = scratch
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s',
                    str(ROOT / 'tests'), '-p', 'test_*.py', '-v'],
                   check=True, env=environment)
    if os.uname().sysname == 'Darwin':
        declarations=(ROOT/'panel/Panel.swift').read_text().split('// Application entry point.',1)[0]
        test_main=Path(scratch)/'main.swift'
        test_main.write_text(declarations+'\n'+(ROOT/'tests/PanelInteractionTests.swift').read_text())
        binary=Path(scratch)/'panel-interaction-tests'
        cache=ROOT/'build'/('swift-module-cache-' + hashlib.sha256(os.fsencode(ROOT)).hexdigest()[:10]);cache.mkdir(parents=True,exist_ok=True)
        subprocess.run(['xcrun','swiftc','-module-cache-path',str(cache),str(test_main),'-o',str(binary)],check=True,env=environment)
        subprocess.run([str(binary)],check=True,env=environment)
