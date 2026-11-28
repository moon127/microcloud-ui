import { render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { renderAt } from "../test/render";
import OAuthCallbackPage from "./OAuthCallbackPage";

afterEach(() => vi.restoreAllMocks());

describe("OAuthCallbackPage (D96)", () => {
  it("finishes the sign-in once, even in StrictMode, and goes back to the channel", async () => {
    const finish = vi.spyOn(api, "finishSignIn").mockResolvedValue({ channel: "gmail", path: "/settings#notifications" });
    render(
      <StrictMode>
        <MemoryRouter initialEntries={["/oauth/callback?state=s1&code=c1"]}>
          <Routes>
            <Route path="/oauth/callback" element={<OAuthCallbackPage />} />
            <Route path="/settings" element={<p>Settings page</p>} />
          </Routes>
        </MemoryRouter>
      </StrictMode>,
    );
    expect(await screen.findByText("Settings page")).toBeInTheDocument();
    expect(finish).toHaveBeenCalledWith("s1", "c1");
    expect(finish).toHaveBeenCalledTimes(1);
  });

  it("shows why it didn't work", async () => {
    vi.spyOn(api, "finishSignIn").mockRejectedValue(new Error("this sign-in was started by another user"));
    renderAt(<OAuthCallbackPage />, { url: "/oauth/callback?state=s1&code=c1" });
    expect(await screen.findByRole("alert")).toHaveTextContent("another user");
  });

  it("reports Google's refusal, and a visit without a sign-in", () => {
    const finish = vi.spyOn(api, "finishSignIn");
    const { unmount } = renderAt(<OAuthCallbackPage />, { url: "/oauth/callback?error=access_denied&state=s" });
    expect(screen.getByRole("alert")).toHaveTextContent("Google didn't sign in: access_denied.");
    unmount();
    // a link's own words aren't shown on the app's page
    const second = renderAt(<OAuthCallbackPage />, { url: "/oauth/callback?error=Your+session+expired,+call+555" });
    expect(screen.getByRole("alert")).toHaveTextContent(/^Google didn't sign in\.$/);
    second.unmount();
    renderAt(<OAuthCallbackPage />, { url: "/oauth/callback" });
    expect(screen.getByRole("alert")).toHaveTextContent("finishes a sign-in");
    expect(finish).not.toHaveBeenCalled();
  });
});
