// Start My Day opens News on the written briefing it speaks, and News > Customize Briefing lists the section skills,
// in both copies of the page (index.html is served; ui_parts/app.html is its hand-kept mirror).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const PAGES = ['index.html', 'ui_parts/app.html'];

const read = file => fs.readFileSync(path.join(root, file), 'utf8').replace(/\r\n/g, '\n');
function block(file) {
  const source = read(file);
  const start = source.indexOf('/* fridayBriefingSections:begin */');
  const end = source.indexOf('/* fridayBriefingSections:end */', start);
  assert.ok(start >= 0 && end > start, file + ': the briefing-sections block is missing');
  return source.slice(start, end);
}

test('the block is the same in both pages', () => {
  assert.equal(block(PAGES[0]), block(PAGES[1]));
});

for (const file of PAGES) {
  test(`${file}: Start My Day's answer opens News on the briefing it names, and nothing without one`, () => {
    const opened = [];
    const context = vm.createContext({window: {fridayOpenWorkspace: t => opened.push(t)}, React: {}, useState() {}, useEffect() {}});
    vm.runInContext(block(file) + ';this.show=fridayShowStartMyDayBriefing', context);
    assert.equal(context.show({briefing: {filename: '2026-11-22.md', date: '2026-11-22'}}), true);
    assert.deepEqual(JSON.parse(JSON.stringify(opened)),
      [{workspace: 'news', tab: 'briefings', briefing: '2026-11-22.md', date: '2026-11-22'}]);
    assert.equal(context.show({briefing: null}), false);
    assert.equal(context.show(null), false);
    assert.equal(opened.length, 1);
  });

  test(`${file}: the Start my day button passes that hook, and the voice helper hands it the answer`, () => {
    const source = read(file);
    assert.match(source, /window\.fridayStartMyDay\s*=\s*\(\)\s*=>\s*startVoiceWithPrompt\('\/api\/voice\/start-my-day',\s*fridayShowStartMyDayBriefing\)/);
    assert.match(source, /startVoiceWithPrompt\s*=\s*useCallback\(async\s*\(url,\s*onData\)\s*=>/);
    assert.match(source, /if\s*\(onData\)\s*onData\(d\);/);
  });

  test(`${file}: News opens a briefing a link names, after refreshing its list`, () => {
    const source = read(file);
    const nav = source.slice(source.indexOf("useNavTarget('news'"), source.indexOf("useNavTarget('news'") + 900);
    assert.match(nav, /if\s*\(t\.briefing\)/);
    assert.match(nav, /fetch\('\/api\/briefings'\)/);
    assert.match(nav, /openBriefing\(\{\s*md:\s*t\.briefing/);
  });

  test(`${file}: Customize Briefing mounts the section-skill list before the local-beat settings`, () => {
    const source = read(file);
    assert.match(source, /(<NewsSectionSkills\/>\s*<NewsLocalBeatSettings\/>|React\.createElement\(NewsSectionSkills, null\), \/\*#__PURE__\*\/React\.createElement\(NewsLocalBeatSettings, null\))/);
    const b = block(file);
    for (const word of ['awaiting approval', 'not installed', "/api/briefing/sections", "Didn\\'t run"]) {
      assert.ok(b.includes(word), file + ': missing ' + word);
    }
  });
}
