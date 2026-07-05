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
  specs: ProductSpec[];
  features: string[];
  includes: string[];
}

export const products: Product[] = [
  {
    id: "quadcopter",
    name: "Quadcopter",
    tagline: "Autonomous Quadcopter for Any Environment",
    description:
      "Our autonomous quadcopter platform, powered by the NVIDIA Jetson Orin Nano. Built for missions where GPS and communications may be unavailable, it operates seamlessly indoors and outdoors with full on-device intelligence for truly autonomous operations.",
    price: 9000,
    image: "/quadcopter.jpg",
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
      "GPS-denied navigation",
      "Comm-denied autonomous operation",
      "Indoor and outdoor capable",
      "On-device AI reasoning",
      "Real-time obstacle avoidance",
      "Mission planning SDK",
      "Open source software stack",
    ],
    includes: [
      "Quadcopter",
      "2x Intelligent Flight Batteries",
      "Battery Charger",
      "Carrying Case",
      "Spare Propellers (4x)",
      "Quick Start Guide",
    ],
  },
  {
    id: "rover",
    name: "Rover",
    tagline: "Autonomous Ground Rover for Long-Range Patrol",
    description:
      "Our autonomous 4WD ground rover, powered by the same NVIDIA Jetson Orin Nano stack as the Quadcopter. Built for persistent perimeter and facility missions with heavier payloads, quieter operation, and all-weather mobility.",
    price: 4000,
    image: "/rover.jpg",
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
      { label: "Drive", value: "4WD all-terrain", category: "Mobility" },
      { label: "Max Runtime", value: "Up to 4 Hours", category: "Mobility" },
      { label: "Max Payload", value: "5kg", category: "Mobility" },
      { label: "Ingress Protection", value: "IP65", category: "Physical" },
    ],
    features: [
      "GPS-denied navigation",
      "Comm-denied autonomous operation",
      "Long-endurance ground missions",
      "On-device AI reasoning",
      "Real-time obstacle avoidance",
      "Mission planning SDK",
      "Open source software stack",
    ],
    includes: [
      "Rover",
      "High-Capacity Battery Pack",
      "Battery Charger",
      "Carrying Case",
      "Quick Start Guide",
    ],
  },
  {
    id: "fixed-wing",
    name: "Fixed-Wing",
    tagline: "Long-Range Autonomous Fixed-Wing for Wide-Area ISR",
    description:
      "Our long-endurance fixed-wing platform, built on the Skywalker X8 airframe with a Holybro Pixhawk Jetson Baseboard carrying the NVIDIA Jetson Orin NX 16GB. It covers wide areas at cruise speed while running the full autonomy stack on-board — ideal for mapping, survey, and ISR where range and endurance matter most.",
    price: 14000,
    image: "/fixed-wing.jpg",
    badge: "Fixed-Wing",
    specs: [
      { label: "Processor", value: "Jetson Orin NX 16GB", category: "Compute" },
      { label: "AI Performance", value: "Up to 157 TOPS", category: "Compute" },
      { label: "Baseboard", value: "Holybro Pixhawk Jetson Baseboard", category: "Compute" },
      { label: "On-Device AI", value: "Full autonomous reasoning", category: "Compute" },
      { label: "LLM Support", value: "On-device & Cloud LLMs", category: "Compute" },
      { label: "GPS-Denied", value: "Yes, visual-inertial navigation", category: "Navigation" },
      { label: "Comm-Denied", value: "Yes, fully autonomous operation", category: "Navigation" },
      { label: "Environment", value: "Outdoor", category: "Operation" },
      { label: "Front Camera", value: "Intel RealSense D435", category: "Sensors" },
      { label: "Camera Resolution", value: "1920x1080 @ 30fps", category: "Sensors" },
      { label: "Depth Output", value: "1280x720 @ 90fps", category: "Sensors" },
      { label: "Obstacle Avoidance", value: "Yes, 3D depth sensing", category: "Sensors" },
      { label: "Airframe", value: "Skywalker X8 flying wing", category: "Flight" },
      { label: "Wingspan", value: "~2.1 m", category: "Flight" },
      { label: "Cruise Speed", value: "45 MPH", category: "Flight" },
      { label: "Max Endurance", value: "Up to 90 Minutes", category: "Flight" },
      { label: "Range", value: "Up to 60 km", category: "Flight" },
      { label: "Launch", value: "Hand / bungee", category: "Flight" },
      { label: "Max Payload", value: "1kg", category: "Flight" },
      { label: "Ingress Protection", value: "IP54", category: "Physical" },
    ],
    features: [
      "Long-range fixed-wing endurance",
      "GPS-denied navigation",
      "Comm-denied autonomous operation",
      "On-device AI reasoning",
      "Real-time obstacle avoidance",
      "Mission planning SDK",
      "Open source software stack",
    ],
    includes: [
      "Skywalker X8 Airframe (assembled)",
      "2x Flight Batteries",
      "Battery Charger",
      "Ground Station Case",
      "Spare Props & Servos",
      "Quick Start Guide",
    ],
  },
  {
    id: "phrover",
    name: "Phrover",
    tagline: "Phone-Powered Autonomous Rover — Bring Your Own AI",
    description:
      "The most affordable way into autonomy. Phrover is a 4WD ground platform that uses a mounted smartphone for compute and vision — bring your own iPhone or Android and let its NPU and camera drive the mission. No Jetson, no dedicated depth camera, no barrier to entry.",
    price: 1000,
    image: "/phrover.jpg",
    badge: "Phrover",
    specs: [
      { label: "Compute", value: "Bring-your-own smartphone (iOS/Android)", category: "Compute" },
      { label: "AI Performance", value: "Phone NPU + optional cloud", category: "Compute" },
      { label: "On-Device AI", value: "Runs on your phone", category: "Compute" },
      { label: "Camera", value: "Smartphone camera (BYO)", category: "Sensors" },
      { label: "Obstacle Avoidance", value: "Monocular vision via phone", category: "Sensors" },
      { label: "Motor Controller", value: "ESP32 motor-driver bridge", category: "Electronics" },
      { label: "Connectivity", value: "Phone GPS, Wi-Fi & cellular", category: "Navigation" },
      { label: "Environment", value: "Indoor & Outdoor", category: "Operation" },
      { label: "Drive", value: "4WD all-terrain", category: "Mobility" },
      { label: "Max Runtime", value: "Up to 4 Hours", category: "Mobility" },
      { label: "Max Payload", value: "3kg", category: "Mobility" },
      { label: "Ingress Protection", value: "IP54", category: "Physical" },
      { label: "Phone", value: "Not included (BYO)", category: "Physical" },
    ],
    features: [
      "Use your own iPhone or Android",
      "Lowest-cost autonomous platform",
      "Phone-powered AI & vision",
      "4WD all-terrain mobility",
      "Mission planning SDK",
      "Open source software stack",
    ],
    includes: [
      "Phrover 4WD Platform",
      "Adjustable Phone Mount",
      "ESP32 Motor Controller",
      "Battery Pack",
      "Battery Charger",
      "Quick Start Guide (phone not included)",
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
