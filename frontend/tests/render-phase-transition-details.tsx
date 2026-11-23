/**
 * Render the phase-journey Transition Details card to static HTML.
 *
 * The Python test feeds this the payload from get_phase_journey and reads
 * the markup. It does not interpret flows itself.
 */
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { PhaseTransitionChart } from "../components/dashboard/phase-transition-chart";

const payload = JSON.parse(readFileSync(process.argv[2], "utf8"));
const html = renderToStaticMarkup(
  <PhaseTransitionChart phases={payload.phases} transitions={payload.transitions} />,
);
process.stdout.write(html);
