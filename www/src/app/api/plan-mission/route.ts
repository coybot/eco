import { NextRequest, NextResponse } from 'next/server';
import { BedrockRuntimeClient, InvokeModelCommand } from '@aws-sdk/client-bedrock-runtime';
import { DynamoDBClient, UpdateItemCommand } from '@aws-sdk/client-dynamodb';
import { fromIni } from '@aws-sdk/credential-providers';
import type { MissionPlan, EnvironmentType } from '@/lib/mission-types';
import { ENV_CONFIG } from '@/lib/mission-types';

const credentials = process.env.NODE_ENV === 'development'
  ? fromIni({ profile: 'presidio' })
  : undefined;

const client = new BedrockRuntimeClient({ region: 'us-west-2', credentials });
const dynamo = new DynamoDBClient({ region: 'us-east-1', credentials });

const RATE_LIMIT = 100;  // requests per IP per hour
const RATE_TABLE = process.env.RATE_LIMIT_TABLE ?? '';

async function checkRateLimit(req: NextRequest): Promise<boolean> {
  if (!RATE_TABLE) return true; // no table in dev = allow
  const ip = req.headers.get('x-forwarded-for')?.split(',')[0].trim() ?? 'unknown';
  const hour = Math.floor(Date.now() / 3_600_000);
  const pk = `plan#${ip}#${hour}`;
  const ttl = Math.floor(Date.now() / 1000) + 7200; // expire after 2h

  try {
    const res = await dynamo.send(new UpdateItemCommand({
      TableName: RATE_TABLE,
      Key: { pk: { S: pk } },
      UpdateExpression: 'ADD #n :one SET #t = if_not_exists(#t, :ttl)',
      ExpressionAttributeNames: { '#n': 'count', '#t': 'ttl' },
      ExpressionAttributeValues: { ':one': { N: '1' }, ':ttl': { N: String(ttl) } },
      ReturnValues: 'ALL_NEW',
    }));
    const count = Number(res.Attributes?.count?.N ?? 1);
    return count <= RATE_LIMIT;
  } catch {
    return true; // fail open — don't block on DynamoDB errors
  }
}

// Cross-region inference profile (same ID as handler.py Lambda)
const MODEL_ID = 'arn:aws:bedrock:us-west-2:041686205727:inference-profile/us.anthropic.claude-sonnet-4-6';

function makeSystemPrompt(env: EnvironmentType, nQuads: number, nRovers: number) {
  const cfg = ENV_CONFIG[env];
  const [xMin, xMax] = cfg.xRange;
  const [zMin, zMax] = cfg.zRange;
  const quadY = cfg.quadY;
  const roverY = cfg.roverY;

  const quadIds = Array.from({ length: nQuads }, (_, i) => `qc-0${i + 1}`);
  const roverIds = Array.from({ length: nRovers }, (_, i) => `rv-0${i + 1}`);
  const allIds = [...quadIds, ...roverIds];

  return `You are a drone mission planner for a 3D simulation set in a ${env === 'city' ? 'modular city block with roads, buildings, and trees' : 'apartment floor plan with rooms, corridors, and furniture'}.

Fleet (fixed — use exactly these vehicles, no more, no less):
${quadIds.map(id => `- ${id.toUpperCase()}: quadcopter`).join('\n')}
${roverIds.map(id => `- ${id.toUpperCase()}: rover`).join('\n')}

Coordinate bounds:
- X: ${xMin} to ${xMax}
- Y: quadcopters fly at y=${quadY}, rovers drive at y=${roverY}
- Z: ${zMin} to ${zMax}

OUTPUT ONLY this exact JSON schema, no markdown, no explanation:
{
  "environment": "${env}",
  "missionTitle": "<5 words>",
  "vehicles": [
    { "id": "qc-01", "type": "quadcopter", "label": "QC-01" }
  ],
  "waypoints": [
    { "vehicleId": "qc-01", "x": 0, "y": ${quadY}, "z": 0, "action": "scan", "duration": 12, "statusLabel": "scanning" }
  ],
  "targetType": "<singular noun for what is found/counted, e.g. \"car\" not \"cars\">",
  "targetCount": 0,
  "reportLines": ["line 1", "line 2", "line 3"],
  "missionSummary": "one sentence result"
}

Rules:
- vehicles array must contain exactly: ${allIds.map(id => `"${id}"`).join(', ')}
- type MUST be "quadcopter" or "rover" (not "quad")
- label is "QC-01", "RV-01", etc.
- waypoints is a FLAT top-level array — do NOT nest inside vehicles
- each vehicle gets 3-5 waypoints spread across the coordinate space
- "duration" is seconds to hold/act at that waypoint AFTER arriving (e.g. how long to scan/inspect) — it is not travel time, vehicles fly there on their own
- for rendezvous/escort missions targetCount = 0; for counting missions set targetCount to what you actually count in the scene image provided
- keep all coords inside the bounds above`;
}

function buildDefaultPlan(env: EnvironmentType, nQuads: number, nRovers: number): MissionPlan {
  const cfg = ENV_CONFIG[env];
  const [xMin, xMax] = cfg.xRange;
  const [zMin, zMax] = cfg.zRange;
  const xMid = (xMin + xMax) / 2;
  const zMid = (zMin + zMax) / 2;

  const vehicles = [
    ...Array.from({ length: nQuads }, (_, i) => ({
      id: `qc-0${i + 1}`, type: 'quadcopter' as const,
      label: `QC-0${i + 1}`,
    })),
    ...Array.from({ length: nRovers }, (_, i) => ({
      id: `rv-0${i + 1}`, type: 'rover' as const,
      label: `RV-0${i + 1}`,
    })),
  ];

  const waypoints = vehicles.flatMap((v, i) => {
    const spread = (i - vehicles.length / 2) * 4;
    const y = v.type === 'quadcopter' ? cfg.quadY : cfg.roverY;
    return [
      { vehicleId: v.id, x: xMid + spread, y, z: zMid + 4, action: 'move' as const, duration: 10, statusLabel: 'deploying' },
      { vehicleId: v.id, x: xMid + spread, y, z: zMid, action: 'scan' as const, duration: 14, statusLabel: 'scanning' },
      { vehicleId: v.id, x: xMid + spread, y, z: zMid - 4, action: 'report' as const, duration: 7, statusLabel: 'reporting' },
    ];
  });

  return {
    environment: env,
    missionTitle: `${cfg.label} survey`,
    vehicles,
    waypoints,
    targetType: 'object',
    targetCount: 8,
    reportLines: ['Deploying fleet', 'Scanning area', 'Mission complete'],
    missionSummary: 'Survey complete.',
  };
}

function stripMarkdownFences(text: string): string {
  return text.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '').trim();
}

async function planWithOllama(
  instruction: string,
  env: EnvironmentType,
  nQuads: number,
  nRovers: number,
): Promise<MissionPlan> {
  const res = await fetch('http://localhost:11434/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: process.env.OLLAMA_MODEL ?? 'llama3.2',
      stream: false,
      messages: [
        { role: 'system', content: makeSystemPrompt(env, nQuads, nRovers) },
        { role: 'user', content: instruction },
      ],
    }),
  });
  const data = await res.json();
  const rawText: string = data?.message?.content ?? '';
  const plan = JSON.parse(stripMarkdownFences(rawText));
  // Ollama sometimes ignores the schema and writes a different environment value — clamp it.
  plan.environment = env;
  return plan;
}

export async function POST(req: NextRequest) {
  if (!(await checkRateLimit(req))) {
    return NextResponse.json({ error: 'Too many requests' }, { status: 429 });
  }

  let instruction: string;
  let env: EnvironmentType;
  let nQuads: number;
  let nRovers: number;
  let sceneImage: string | undefined;

  try {
    const body = await req.json();
    instruction = typeof body?.instruction === 'string' ? body.instruction.trim() : '';
    env = (body?.environment === 'apartment' ? 'apartment' : 'city') as EnvironmentType;
    nQuads = Math.max(0, Math.min(10, parseInt(body?.quadcopters ?? '1', 10) || 0));
    nRovers = Math.max(0, Math.min(10, parseInt(body?.rovers ?? '1', 10) || 0));
    if (nQuads + nRovers === 0) nQuads = 1;
    sceneImage = typeof body?.sceneImage === 'string' && body.sceneImage.length > 0
      ? body.sceneImage
      : undefined;
  } catch {
    return NextResponse.json(buildDefaultPlan('city', 1, 1));
  }

  if (!instruction) {
    return NextResponse.json(buildDefaultPlan(env, nQuads, nRovers));
  }

  if (process.env.OFFLINE_MODE === 'true') {
    try {
      console.log('[plan-mission] offline mode — using local Ollama');
      const plan = await planWithOllama(instruction, env, nQuads, nRovers);
      return NextResponse.json(plan);
    } catch (err) {
      console.error('[plan-mission] Ollama error, using default plan:', err);
      return NextResponse.json(buildDefaultPlan(env, nQuads, nRovers));
    }
  }

  try {
    const userContent: unknown[] = [];
    if (sceneImage) {
      userContent.push({
        type: 'image',
        source: { type: 'base64', media_type: 'image/jpeg', data: sceneImage },
      });
      userContent.push({
        type: 'text',
        text: `Above is a screenshot of the 3D simulation scene. ${instruction || 'Search the area and report.'}`,
      });
    } else {
      userContent.push({ type: 'text', text: instruction || 'Search the area and report.' });
    }

    const payload = {
      anthropic_version: 'bedrock-2023-05-31',
      max_tokens: 2000,
      system: makeSystemPrompt(env, nQuads, nRovers),
      messages: [{ role: 'user', content: userContent }],
    };

    const command = new InvokeModelCommand({
      modelId: MODEL_ID,
      contentType: 'application/json',
      accept: 'application/json',
      body: JSON.stringify(payload),
    });

    const response = await client.send(command);
    const responseBody = JSON.parse(new TextDecoder().decode(response.body));
    const rawText: string = responseBody?.content?.[0]?.text ?? '';
    const plan: MissionPlan = JSON.parse(stripMarkdownFences(rawText));
    return NextResponse.json(plan);
  } catch (err) {
    console.error('[plan-mission] Bedrock error:', err);
    return NextResponse.json(buildDefaultPlan(env, nQuads, nRovers));
  }
}
