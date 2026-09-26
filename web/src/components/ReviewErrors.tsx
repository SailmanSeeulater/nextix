import { TriangleAlert } from "lucide-react";
import { stepWord } from "@/lib/artifacts";
import type { ReviewError } from "@/lib/types";

/**
 * Review steps that went wrong without failing the run (setup, the app, a screenshot,
 * a rejected file), shown where their output would have been.
 */
export function ReviewErrors({ errors }: { errors: readonly ReviewError[] }) {
  if (errors.length === 0) return null;
  return (
    <ul className="review-errors">
      {errors.map((e, i) => (
        <li key={i}>
          <TriangleAlert size={15} strokeWidth={2.5} aria-hidden />
          <p>
            <span className="review-error-step">{stepWord(e.step)}:</span> {e.message}
          </p>
        </li>
      ))}
    </ul>
  );
}
