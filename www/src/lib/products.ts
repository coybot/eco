export interface ProductSpec {
  label: string;
  value: string;
  category: string;
}

export interface Product {
  id: string;
  name: string;
  tagline: string;
  description: string;
  price: number;
  image: string;
  badge: string;
  stripeProductId?: string;
  specs: ProductSpec[];
  features: string[];
  includes: string[];
}

export const products: Product[] = [
  {
    id: "m1a",
    name: "M1-A",
    tagline: "Autonomous Quadcopter for Any Environment",
    description:
      "The M1-A is our autonomous quadcopter platform, powered by the NVIDIA Jetson Orin Nano. Built for missions where GPS and communications may be unavailable, it operates seamlessly indoors and outdoors with full on-device intelligence for truly autonomous operations.",
    price: 5299,
    image: "/m1a.png",
    badge: "Quadcopter",
    specs: [
      { label: "Processor", value: "Jetson Orin Nano 8GB", category: "Compute" },
      { label: "AI Performance", value: "Up to 67 TOPS", category: "Compute" },
      { label: "On-Device AI", value: "Full autonomous reasoning", category: "Compute" },
      { label: "LLM Support", value: "On-device & Cloud LLMs", category: "Compute" },
      { label: "GPS-Denied", value: "Yes, visual-inertial navigation", category: "Navigation" },
      { label: "Comm-Denied", value: "Yes, fully autonomous operation", category: "Navigation" },
      { label: "Environment", value: "Indoor & Outdoor", category: "Operation" },
      { label: "Front Camera", value: "Intel RealSense D435", category: "Sensors" },
      { label: "Camera Resolution", value: "1920x1080 @ 30fps", category: "Sensors" },
      { label: "Depth Output", value: "1280x720 @ 90fps", category: "Sensors" },
      { label: "Obstacle Avoidance", value: "Yes, 3D depth sensing", category: "Sensors" },
      { label: "Max Flight Time", value: "Up to 30 Minutes", category: "Flight" },
      { label: "Max Payload", value: "500g", category: "Flight" },
      { label: "Ingress Protection", value: "IP55", category: "Physical" },
    ],
    features: [
      "NDAA-compliant compute & sensors",
      "GPS-denied navigation",
      "Comm-denied autonomous operation",
      "Indoor and outdoor capable",
      "On-device AI reasoning",
      "Real-time obstacle avoidance",
      "Mission planning SDK",
      "Open source software stack",
    ],
    includes: [
      "M1-A Quadcopter",
      "2x Intelligent Flight Batteries",
      "Battery Charger",
      "Carrying Case",
      "Spare Propellers (4x)",
      "Quick Start Guide",
    ],
  },
  {
    id: "m1g",
    name: "M1-G",
    tagline: "Autonomous Ground Rover for Long-Range Patrol",
    description:
      "The M1-G is our autonomous ground rover, powered by the same NVIDIA Jetson Orin Nano stack as the M1-A. Built for persistent perimeter and facility missions with heavier payloads, quieter operation, and all-weather mobility.",
    price: 7499,
    image: "/m1g.png",
    badge: "Rover",
    specs: [
      { label: "Processor", value: "Jetson Orin Nano 8GB", category: "Compute" },
      { label: "AI Performance", value: "Up to 67 TOPS", category: "Compute" },
      { label: "On-Device AI", value: "Full autonomous reasoning", category: "Compute" },
      { label: "LLM Support", value: "On-device & Cloud LLMs", category: "Compute" },
      { label: "GPS-Denied", value: "Yes, visual-inertial navigation", category: "Navigation" },
      { label: "Comm-Denied", value: "Yes, fully autonomous operation", category: "Navigation" },
      { label: "Environment", value: "Indoor & Outdoor", category: "Operation" },
      { label: "Front Camera", value: "Intel RealSense D435", category: "Sensors" },
      { label: "Camera Resolution", value: "1920x1080 @ 30fps", category: "Sensors" },
      { label: "Depth Output", value: "1280x720 @ 90fps", category: "Sensors" },
      { label: "Obstacle Avoidance", value: "Yes, 3D depth sensing", category: "Sensors" },
      { label: "Max Runtime", value: "Up to 4 Hours", category: "Mobility" },
      { label: "Max Payload", value: "5kg", category: "Mobility" },
      { label: "Ingress Protection", value: "IP65", category: "Physical" },
    ],
    features: [
      "NDAA-compliant compute & sensors",
      "GPS-denied navigation",
      "Comm-denied autonomous operation",
      "Long-endurance ground missions",
      "On-device AI reasoning",
      "Real-time obstacle avoidance",
      "Mission planning SDK",
      "Open source software stack",
    ],
    includes: [
      "M1-G Rover",
      "High-Capacity Battery Pack",
      "Battery Charger",
      "Carrying Case",
      "Quick Start Guide",
    ],
  },
];

export function getProduct(id: string): Product | undefined {
  return products.find((p) => p.id === id);
}

export function formatPrice(price: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
  }).format(price);
}
