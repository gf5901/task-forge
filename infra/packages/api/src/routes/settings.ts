import { Hono } from "hono";
import { GetCommand, UpdateCommand } from "@aws-sdk/lib-dynamodb";
import { ddb, TABLE_NAME } from "../lib/dynamo.js";

export const settings = new Hono();

const CONFIG_PK = "CONFIG#GLOBAL";
const CONFIG_SK = "SETTINGS";

const DEFAULTS: Record<string, number> = {
  max_concurrent_runners: 1,
  min_spawn_interval: 300,
  task_timeout: 900,
  budget_daily_usd: 0,
};

const VALIDATORS: Record<string, (v: number) => boolean> = {
  max_concurrent_runners: (v) => Number.isInteger(v) && v >= 1 && v <= 4,
  min_spawn_interval: (v) => Number.isInteger(v) && v >= 0 && v <= 3600,
  task_timeout: (v) => Number.isInteger(v) && v >= 60 && v <= 3600,
  budget_daily_usd: (v) => v >= 0 && v <= 1000,
};

async function getSettings(): Promise<Record<string, number>> {
  const resp = await ddb.send(
    new GetCommand({ TableName: TABLE_NAME, Key: { pk: CONFIG_PK, sk: CONFIG_SK } })
  );
  const item = resp.Item ?? {};
  const merged = { ...DEFAULTS };
  for (const key of Object.keys(DEFAULTS)) {
    if (key in item) merged[key] = Number(item[key]);
  }
  return merged;
}

settings.get("/settings", async (c) => {
  return c.json(await getSettings());
});

settings.patch("/settings", async (c) => {
  const body = await c.req.json();
  const errors: string[] = [];
  const clean: Record<string, number> = {};

  for (const [key, raw] of Object.entries(body)) {
    if (!(key in DEFAULTS)) {
      errors.push(`unknown setting: ${key}`);
      continue;
    }
    if (raw == null) continue;
    const val = Number(raw);
    if (isNaN(val)) {
      errors.push(`${key}: invalid number`);
      continue;
    }
    const validate = VALIDATORS[key];
    if (validate && !validate(val)) {
      errors.push(`${key}: value ${val} out of range`);
      continue;
    }
    clean[key] = val;
  }

  if (errors.length) return c.json({ error: errors.join("; ") }, 400);
  if (Object.keys(clean).length === 0) return c.json(await getSettings());

  const names: Record<string, string> = {};
  const values: Record<string, number> = {};
  const parts: string[] = [];
  let i = 0;
  for (const [key, val] of Object.entries(clean)) {
    const alias = `#k${i}`;
    const placeholder = `:v${i}`;
    parts.push(`${alias} = ${placeholder}`);
    names[alias] = key;
    values[placeholder] = val;
    i++;
  }

  await ddb.send(
    new UpdateCommand({
      TableName: TABLE_NAME,
      Key: { pk: CONFIG_PK, sk: CONFIG_SK },
      UpdateExpression: `SET ${parts.join(", ")}`,
      ExpressionAttributeNames: names,
      ExpressionAttributeValues: values,
    })
  );

  return c.json(await getSettings());
});
