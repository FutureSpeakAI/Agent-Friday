"""Local source searches distinguish literal patterns from executable shell text."""
import pytest

from agent_friday.governance import action_gate


@pytest.mark.parametrize("command", [
    "Select-String -Path 'index.html' -Pattern '/api/wiki'",
    'Select-String -Path "index.html" -Pattern "/api/wiki","localhost" | Select-Object -First 60',
    "Get-Content 'index.html' | Select-String -Pattern '/api/wiki|openWikiPage' | Select-Object -First 60",
    "sls -Path 'index.html' -Pattern 'marked.parse|wiki_page' | select -First 60",
    "Select-String -Path 'index.html' -Pattern '<a target=\"_blank\">'",
    "Select-String -Path 'index.html' -Pattern 'invoke-restmethod; new-object'",
    "Select-String -Path 'index.html' -Pattern 'Friday''s /api/wiki'",
])
def test_literal_source_search_is_internal(command):
    assert action_gate.classify_command(command)[0] == action_gate.INTERNAL


@pytest.mark.parametrize("command", [
    "Invoke-RestMethod 'http://localhost:3000/api/settings'",
    "curl 'http://127.0.0.1:3000/api/approvals'",
    "Get-Content 'http://[::1]:3000/api/settings'",
    "Select-String -Path 'http://localhost:3000/api/settings' -Pattern 'hello'",
    "Select-String -InputObject 'http://localhost:3000/api/settings' -Pattern 'hello'",
    'Select-String -Path "index.html" -Pattern "$(Invoke-RestMethod http://localhost:3000/api/settings)"',
    "Select-String -Path 'index.html' -Pattern '/api/wiki' | Invoke-RestMethod",
    "Select-String -Path 'index.html' -Pattern '/api/wiki'; Invoke-RestMethod $url",
    "Select-String -Path 'index.html' -Pattern '/api/wiki' | ForEach-Object { Invoke-RestMethod $_ }",
    "Select-String -Path 'index.html' -Pattern /api/wiki",
    "Select-String -Path 'index.html' -Pattern '/api/wiki",
    "Select-String -Path 'index.html' -Pattern '/api/wiki\u2019; Invoke-RestMethod $url #'",
    'Select-String -Path "index.html" -Pattern "/api/wiki`"; Invoke-RestMethod $url #"',
])
def test_local_api_is_still_forbidden_outside_literal_search_patterns(command):
    assert action_gate.classify_command(command)[0] == "forbidden"


def test_scriptblock_formatting_still_needs_a_decision():
    command = "Select-String -Path 'index.html' -Pattern 'wiki_page' | ForEach-Object { $_.Line }"
    assert action_gate.classify_command(command)[0] == action_gate.OUTWARD
