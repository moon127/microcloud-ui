import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router";
import Layout from "./Layout";
import RangePicker from "./RangePicker";
import { useRange } from "./range";
import { useSession } from "./session";
import { renderAt } from "./test/render";

function RangeProbe() {
  return (
    <>
      <RangePicker />
      <p>range={useRange()}</p>
    </>
  );
}

describe("Layout and range", () => {
  it("shows nav, user and sign-out", () => {
    const { session } = renderAt(
      <Routes>
        <Route element={<Layout />}>
          <Route path="*" element={<p>child</p>} />
        </Route>
      </Routes>,
      { url: "/storage" },
    );
    expect(screen.getByText("child")).toBeInTheDocument();
    expect(screen.getByText("alice (admin)")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Storage" })).toHaveClass("active");
    expect(screen.getByRole("link", { name: "Overview" })).not.toHaveClass("active");
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(session.onLogout).toHaveBeenCalled();
  });

  it("reads and sets the range in the URL, defaulting to 1h", () => {
    renderAt(<RangeProbe />, { url: "/x?range=12345&keep=1" });
    expect(screen.getByText("range=3600")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "1h" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "7d" }));
    expect(screen.getByText("range=604800")).toBeInTheDocument();
  });
});

it("useSession needs a provider", () => {
  function Bare() {
    useSession();
    return null;
  }
  vi.spyOn(console, "error").mockImplementation(() => {});
  expect(() => render(<Bare />)).toThrow("useSession outside SessionContext");
});
