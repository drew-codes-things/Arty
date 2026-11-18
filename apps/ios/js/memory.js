export const MAX_NAME_CHARS = 60;
export const MAX_FACT_CHARS = 300;
const PERSON = "person";

export function matchingEntities(entities, query) {
  const wanted = query.trim().toLowerCase();
  if (!wanted) return entities;
  return entities.filter((entity) => [entity.name, entity.entityType, ...(entity.observations ?? [])]
    .some((text) => String(text ?? "").toLowerCase().includes(wanted)));
}

export function likelyOwner(entities) {
  const people = entities.filter((entity) => entity.entityType === PERSON);
  const busiest = [...people].sort((a, b) => (b.observations?.length ?? 0) - (a.observations?.length ?? 0))[0];
  return busiest?.name ?? "";
}
