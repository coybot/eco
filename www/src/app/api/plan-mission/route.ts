import { NextRequest, NextResponse } from 'next/server';
import { BedrockRuntimeClient, InvokeModelCommand } from '@aws-sdk/client-bedrock-runtime';
import { fromIni } from '@aws-sdk/credential-providers';
import type { MissionPlan, EnvironmentType } from '@/lib/mission-types';
import { ENV_CONFIG } from '@/lib/mission-types';

const credentials = process.env.NODE_ENV === 'development'
  ? fromIni({ profile: 'astral' })
  : undefined;

const client = new BedrockRuntimeClient({ region: 'us-west-2', credentials });

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
    { "vehicleId": "qc-01", "x": 0, "y": ${quadY}, "z": 0, "action": "scan", "duration": 3, "statusLabel": "scanning" }
  ],
  "targetType": "<what is being found/counted>",
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
- for counting missions targetCount = 8-15, for rendezvous targetCount = 0
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
      { vehicleId: v.id, x: xMid + spread, y, z: zMid + 4, action: 'move' as const, duration: 2, statusLabel: 'deploying' },
      { vehicleId: v.id, x: xMid + spread, y, z: zMid, action: 'scan' as const, duration: 4, statusLabel: 'scanning' },
      { vehicleId: v.id, x: xMid + spread, y, z: zMid - 4, action: 'report' as const, duration: 2, statusLabel: 'reporting' },
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

export async function POST(req: NextRequest) {
  let instruction: string;
  let env: EnvironmentType;
  let nQuads: number;
  let nRovers: number;

  try {
    const body = await req.json();
    instruction = typeof body?.instruction === 'string' ? body.instruction.trim() : '';
    env = (body?.environment === 'apartment' ? 'apartment' : 'city') as EnvironmentType;
    nQuads = Math.max(0, Math.min(10, parseInt(body?.quadcopters ?? '1', 10) || 0));
    nRovers = Math.max(0, Math.min(10, parseInt(body?.rovers ?? '1', 10) || 0));
    if (nQuads + nRovers === 0) nQuads = 1; // need at least one vehicle
  } catch {
    return NextResponse.json(buildDefaultPlan('city', 1, 1));
  }

  if (!instruction) {
    return NextResponse.json(buildDefaultPlan(env, nQuads, nRovers));
  }

  try {
    const payload = {
      anthropic_version: 'bedrock-2023-05-31',
      max_tokens: 2000,
      system: makeSystemPrompt(env, nQuads, nRovers),
      messages: [{ role: 'user', content: instruction }],
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
