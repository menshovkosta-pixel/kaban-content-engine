import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.ts";

test("project UI route is served only through static assets binding", async () => {
  let seen="";
  const env:any={ASSETS:{fetch:async(req:Request)=>{seen=new URL(req.url).pathname;return new Response("ui",{status:200});}}};
  const response=await worker.fetch(new Request("https://worker.example/projects/caelus/"),env);
  assert.equal(response.status,200);
  assert.equal(await response.text(),"ui");
  assert.equal(seen,"/projects/caelus/");
});
