import test from "node:test";
import assert from "node:assert/strict";
import { createCommandCoordinator, StaleVersionError } from "../../../../projects/caelus/cloud_ui/app.js";

test("stale 409 is surfaced and mutation is not automatically retried", async () => {
  let calls=0;
  const coordinator=createCommandCoordinator({
    uuid:()=>"11111111-1111-4111-8111-111111111111",
    fetchImpl:async()=>{calls++;return new Response(JSON.stringify({error:"VERSION_CONFLICT"}),{status:409,headers:{"Content-Type":"application/json"}});},
  });
  await assert.rejects(
    ()=>coordinator.post({actionKey:"save",operation:"save",contentKey:"2026-09-26:ru",expectedVersion:7,payload:{}}),
    StaleVersionError,
  );
  assert.equal(calls,1);
});

test("duplicate click while request is in flight causes one backend command", async () => {
  let calls=0; let release!:()=>void;
  const gate=new Promise<void>(resolve=>{release=resolve;});
  let seq=0;
  const coordinator=createCommandCoordinator({
    uuid:()=>`00000000-0000-4000-8000-${String(++seq).padStart(12,"0")}`,
    fetchImpl:async(_url:any,init:any)=>{calls++;await gate;return new Response(init.body,{status:202,headers:{"Content-Type":"application/json"}});},
  });
  const a=coordinator.post({actionKey:"approve",operation:"approve",contentKey:"2026-09-26:ru",expectedVersion:7,payload:{}});
  const b=coordinator.post({actionKey:"approve",operation:"approve",contentKey:"2026-09-26:ru",expectedVersion:7,payload:{}});
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(calls,1);
  release();
  const [first,second]=await Promise.all([a,b]);
  assert.equal(first.idempotency_key,second.idempotency_key);
  assert.equal(seq,2);
});
