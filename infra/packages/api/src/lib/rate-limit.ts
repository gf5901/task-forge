import { GetCommand, UpdateCommand } from "@aws-sdk/lib-dynamodb";
import { ddb, TABLE_NAME } from "./dynamo.js";

const MAX_ATTEMPTS = 5;
const WINDOW_SECONDS = 15 * 60; // 15 minutes

/**
 * Check if an IP is currently rate-limited.
 * Returns the number of seconds remaining if blocked, or 0 if allowed.
 */
export async function checkRateLimit(ip: string): Promise<number> {
  const now = Math.floor(Date.now() / 1000);
  const resp = await ddb.send(
    new GetCommand({
      TableName: TABLE_NAME,
      Key: { pk: `RATELIMIT#${ip}`, sk: "LOGIN" },
    })
  );
  const item = resp.Item;
  if (!item) return 0;

  const ttl = (item.ttl as number) ?? 0;
  if (ttl <= now) return 0; // expired entry

  const attempts = (item.attempts as number) ?? 0;
  if (attempts >= MAX_ATTEMPTS) {
    return ttl - now;
  }
  return 0;
}

/**
 * Record a failed login attempt for an IP.
 */
export async function recordFailedAttempt(ip: string): Promise<void> {
  const ttl = Math.floor(Date.now() / 1000) + WINDOW_SECONDS;
  await ddb.send(
    new UpdateCommand({
      TableName: TABLE_NAME,
      Key: { pk: `RATELIMIT#${ip}`, sk: "LOGIN" },
      UpdateExpression: "SET attempts = if_not_exists(attempts, :zero) + :one, #t = :ttl",
      ExpressionAttributeNames: { "#t": "ttl" },
      ExpressionAttributeValues: { ":zero": 0, ":one": 1, ":ttl": ttl },
    })
  );
}

/**
 * Clear rate limit state for an IP after successful login.
 */
export async function clearRateLimit(ip: string): Promise<void> {
  await ddb.send(
    new UpdateCommand({
      TableName: TABLE_NAME,
      Key: { pk: `RATELIMIT#${ip}`, sk: "LOGIN" },
      UpdateExpression: "SET attempts = :zero, #t = :zero",
      ExpressionAttributeNames: { "#t": "ttl" },
      ExpressionAttributeValues: { ":zero": 0 },
    })
  );
}
