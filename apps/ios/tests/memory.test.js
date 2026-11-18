import assert from "node:assert/strict";
import { test } from "node:test";
import { likelyOwner, matchingEntities } from "../js/memory.js";

const ENTITIES = [
  { name: "Ella", entityType: "person", observations: ["Rowan's sister"] },
  { name: "Rowan", entityType: "person", observations: ["Lives in Bristol", "Plays five-a-side"] },
  { name: "Garden", entityType: "topic", observations: ["Has a pond"] },
];

test("search matches names, types and facts, ignoring case", () => {
  assert.deepEqual(matchingEntities(ENTITIES, "bristol").map((e) => e.name), ["Rowan"]);
  assert.deepEqual(matchingEntities(ENTITIES, "PERSON").map((e) => e.name), ["Ella", "Rowan"]);
  assert.deepEqual(matchingEntities(ENTITIES, "  ").map((e) => e.name), ["Ella", "Rowan", "Garden"]);
  assert.deepEqual(matchingEntities(ENTITIES, "nothing like it"), []);
});

test("a new fact is about the person with the most facts unless the user changes it", () => {
  assert.equal(likelyOwner(ENTITIES), "Rowan");
  assert.equal(likelyOwner([{ name: "Garden", entityType: "topic", observations: [] }]), "");
  assert.equal(likelyOwner([]), "");
});
