import MapRouter from "./MapRouter";
import renderRoot from "./renderRoot";

export default function mountBigMap(element: HTMLElement) {
  renderRoot(element, <MapRouter />);
}
