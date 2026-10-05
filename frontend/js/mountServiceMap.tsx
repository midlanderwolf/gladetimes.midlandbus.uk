import ServiceMap from "./ServiceMap";
import renderRoot from "./renderRoot";

export default function mountServiceMap(
  element: HTMLElement,
  serviceId: number,
) {
  renderRoot(
    element,
    <ServiceMap serviceId={serviceId} buttonText={element.innerText} />,
  );
}
