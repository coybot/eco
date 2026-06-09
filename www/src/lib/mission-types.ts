export type VehicleType = 'quadcopter' | 'rover';
export type EnvironmentType = 'city' | 'apartment';
export type ActionType = 'move' | 'scan' | 'hover' | 'inspect' | 'rendezvous' | 'report';

export interface Vehicle {
  id: string;       // e.g. "qd-01", "rv-01"
  type: VehicleType;
  label: string;    // e.g. "QD-01", "RV-01"
}

export interface Waypoint {
  vehicleId: string;
  x: number;
  y: number;
  z: number;
  action: ActionType;
  duration: number;     // seconds to hold here
  statusLabel: string;  // short label shown in overlay, e.g. "scanning", "approaching"
}

export interface MissionPlan {
  environment: EnvironmentType;
  missionTitle: string;
  vehicles: Vehicle[];
  waypoints: Waypoint[];
  targetType: string;
  targetCount: number;
  reportLines: string[];
  missionSummary: string;
}

// ENV_CONFIG drives camera, spawn positions, and coord bounds per environment
export const ENV_CONFIG: Record<EnvironmentType, {
  model: string;
  camera: [number, number, number];
  target: [number, number, number];
  fov: number;
  quadY: number;
  roverY: number;
  xRange: [number, number];
  zRange: [number, number];
  envPreset: string;
  label: string;
}> = {
  city: {
    model: '/models/neighbourhood.glb',
    camera: [3, 26, 36],
    target: [3, 2, -2],
    fov: 52,
    quadY: 8,
    roverY: 0.3,
    xRange: [-14, 20],
    zRange: [-18, 14],
    envPreset: 'city',
    label: 'City block',
  },
  apartment: {
    model: '/models/apartment.glb',
    camera: [4, 16, 16],
    target: [4, 0, 2],
    fov: 55,
    quadY: 1.8,
    roverY: 0.2,
    xRange: [-8, 17],
    zRange: [-1, 5],
    envPreset: 'apartment',
    label: 'Apartment',
  },
};
