import { useEffect, useEffectEvent, useRef } from "react";
import * as THREE from "three";
import type { Rig } from "./lighting";

const RADIUS = 2;

/** Where a light sits on the guide sphere: azimuth 0 faces the camera
 * (+z), 90 is screen right (+x), elevation 90 is straight up (+y). */
export function place(azimuth: number, elevation: number): THREE.Vector3 {
  const az = THREE.MathUtils.degToRad(azimuth);
  const el = THREE.MathUtils.degToRad(elevation);
  return new THREE.Vector3(Math.cos(el) * Math.sin(az), Math.sin(el), Math.cos(el) * Math.cos(az)).multiplyScalar(
    RADIUS,
  );
}

export function angles(point: THREE.Vector3): { azimuth: number; elevation: number } {
  const p = point.clone().normalize();
  const azimuth = (THREE.MathUtils.radToDeg(Math.atan2(p.x, p.z)) + 360) % 360;
  const elevation = THREE.MathUtils.radToDeg(Math.asin(THREE.MathUtils.clamp(p.y, -1, 1)));
  return { azimuth: Math.round(azimuth) % 360, elevation: Math.round(elevation) };
}

interface Scene {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  lights: THREE.Group;
  markers: THREE.Group;
  ambient: THREE.AmbientLight;
}

/** The rig editor's sphere (M5.5, three.js): a head lit by the rig, each
 * light a dot on a guide sphere; drag a dot to move its light. */
export default function LightSphere({
  rig,
  selected,
  onSelect,
  onMove,
}: {
  rig: Rig;
  selected: number;
  onSelect: (index: number) => void;
  onMove: (index: number, azimuth: number, elevation: number) => void;
}) {
  const mount = useRef<HTMLDivElement>(null);
  const state = useRef<Scene | null>(null);
  const select = useEffectEvent((index: number) => onSelect(index));
  const moveTo = useEffectEvent((index: number, azimuth: number, elevation: number) =>
    onMove(index, azimuth, elevation),
  );

  useEffect(() => {
    const host = mount.current;
    if (!host) return;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    host.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 50);
    camera.position.set(0, 0.6, 6.4);
    camera.lookAt(0, 0, 0);

    // The subject: a head with a nose, so the light's direction reads.
    const skin = new THREE.MeshStandardMaterial({ color: 0xd8d0c4, roughness: 0.55 });
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.9, 64, 48), skin);
    const nose = new THREE.Mesh(new THREE.ConeGeometry(0.13, 0.32, 24), skin);
    nose.rotation.x = Math.PI / 2;
    nose.position.set(0, -0.05, 0.92);
    const shoulders = new THREE.Mesh(new THREE.SphereGeometry(1.2, 48, 24, 0, Math.PI * 2, 0, Math.PI / 2), skin);
    shoulders.scale.set(1.3, 0.6, 0.8);
    shoulders.position.set(0, -1.55, 0);
    scene.add(head, nose, shoulders);
    const guide = new THREE.LineSegments(
      new THREE.WireframeGeometry(new THREE.SphereGeometry(RADIUS, 24, 12)),
      new THREE.LineBasicMaterial({ color: 0x8a94a6, transparent: true, opacity: 0.18 }),
    );
    scene.add(guide);
    const ambient = new THREE.AmbientLight(0xffffff, 0.2);
    const lights = new THREE.Group();
    const markers = new THREE.Group();
    scene.add(ambient, lights, markers);
    state.current = { renderer, scene, camera, lights, markers, ambient };

    const resize = () => {
      const size = host.clientWidth;
      renderer.setSize(size, size, false);
      renderer.domElement.style.width = "100%";
      renderer.domElement.style.height = "100%";
      camera.aspect = 1;
      camera.updateProjectionMatrix();
      renderer.render(scene, camera);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();

    // Dragging a marker moves its light over the guide sphere.
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const sphere = new THREE.Sphere(new THREE.Vector3(), RADIUS);
    let dragging: number | null = null;
    const aim = (event: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.set(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1,
      );
      raycaster.setFromCamera(pointer, camera);
    };
    const down = (event: PointerEvent) => {
      aim(event);
      const [hit] = raycaster.intersectObjects(markers.children, false);
      const index = hit ? markers.children.indexOf(hit.object) : -1;
      if (index < 0) return;
      dragging = index;
      select(index);
      renderer.domElement.setPointerCapture(event.pointerId);
    };
    const move = (event: PointerEvent) => {
      if (dragging === null) return;
      aim(event);
      const point = raycaster.ray.intersectSphere(sphere, new THREE.Vector3());
      if (!point) return;
      const { azimuth, elevation } = angles(point);
      moveTo(dragging, azimuth, elevation);
    };
    const up = () => {
      dragging = null;
    };
    const canvas = renderer.domElement;
    canvas.addEventListener("pointerdown", down);
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
    canvas.style.touchAction = "none";
    return () => {
      observer.disconnect();
      canvas.removeEventListener("pointerdown", down);
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up);
      renderer.dispose();
      host.removeChild(canvas);
      state.current = null;
    };
  }, []);

  useEffect(() => {
    const s = state.current;
    if (!s) return;
    s.lights.clear();
    s.markers.clear();
    s.ambient.intensity = 0.08 + (rig.ambient ?? 0.1) * 1.6;
    rig.lights.forEach((light, index) => {
      const position = place(light.azimuth, light.elevation);
      const colour = new THREE.Color(light.colour);
      const intensity = light.intensity ?? 1;
      const softness = light.softness ?? 0.5;
      const lamp = new THREE.SpotLight(colour, intensity * 60, 0, 0.5 + softness * 0.45, softness);
      lamp.position.copy(position);
      lamp.target.position.set(0, 0, 0);
      s.lights.add(lamp, lamp.target);
      const marker = new THREE.Mesh(
        new THREE.SphereGeometry(index === selected ? 0.17 : 0.12, 24, 16),
        new THREE.MeshBasicMaterial({ color: colour, depthTest: false, transparent: true, opacity: 0.95 }),
      );
      marker.position.copy(position);
      marker.renderOrder = 2;
      s.markers.add(marker);
    });
    s.renderer.render(s.scene, s.camera);
  }, [rig, selected]);

  return <div ref={mount} className="aspect-square w-full cursor-grab" data-testid="light-sphere" />;
}
