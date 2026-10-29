const {test}=require('node:test'), assert=require('node:assert/strict'), vm=require('node:vm'), fs=require('node:fs');
const source=fs.readFileSync('static/workbench.js','utf8');
const render=source.slice(source.indexOf('  function renderMetrics(s)'),source.indexOf('  function setMeter('));
test('delayed provider invoice cannot make the budget meter understate the stop estimate',()=>{
  const nodes=new Map(), ratios=new Map();
  const context={$:id=>{if(!nodes.has(id))nodes.set(id,{id});return nodes.get(id);},S:{elapsed:60},fmtDur:String,money:v=>`¥${v.toFixed(2)}`,
    setMeter:(node,label,ratio)=>ratios.set(node.id,ratio)};
  vm.createContext(context);vm.runInContext(render,context);
  context.renderMetrics({billing:{credits_cost:30,estimate_credits:100,billed_seconds:20,rmb:.94},budget_credits:200,max_session_minutes:2,credit_price_rmb:.03125});
  assert.equal(ratios.get('meterBudget'),.5);
  assert.equal(nodes.get('mCredits').textContent,'≈100');
  assert.equal(nodes.get('mRmb').textContent,'≈¥3.13');
});

test('free rehearsal does not show a paid-session time limit or exhausted time meter',()=>{
  const nodes=new Map(),ratios=new Map(),context={$:id=>{if(!nodes.has(id))nodes.set(id,{id});return nodes.get(id);},S:{elapsed:180},fmtDur:String,money:v=>`¥${v.toFixed(2)}`,setMeter:(node,label,ratio)=>ratios.set(node.id,ratio)};
  vm.createContext(context);vm.runInContext(render,context);
  context.renderMetrics({mode:'dry_run',billing:{},budget_credits:200,max_session_minutes:2,credit_price_rmb:.03125});
  assert.equal(nodes.get('mElapsedSub').textContent,'演练不限时');
  assert.equal(ratios.get('meterTime'),0);
});
