import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

describe("processing workspace", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("exposes source upload, dithering controls and preset saving", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "Frame Studio" })).toBeInTheDocument();
    expect(screen.getByLabelText("Source image")).toHaveAttribute("type", "file");
    expect(screen.getByLabelText("Dithering algorithm")).toBeInTheDocument();
    expect(screen.getByLabelText("Brightness")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save preset" })).toBeInTheDocument();
  });

  it("uploads a source and displays the backend preview", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        draft_id: "7f3a2cb2-a908-4216-84e4-7987127e7acc",
        revision: 1,
        preview_url: "/api/v1/drafts/7f3a2cb2-a908-4216-84e4-7987127e7acc/preview.png",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    const file = new File(["png"], "weather.png", { type: "image/png" });

    await userEvent.upload(screen.getByLabelText("Source image"), file);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/drafts",
      expect.objectContaining({ method: "POST", body: expect.any(FormData) }),
    );
    const submitted = fetchMock.mock.calls[0][1].body as FormData;
    expect(submitted.get("dithering")).toBe("good-display-floyd-v1");
    expect(submitted.get("brightness")).toBe("0");
    expect(submitted.get("contrast")).toBe("0");
    expect(submitted.get("saturation")).toBe("0");
    expect(await screen.findByRole("img", { name: "Spectra 6 preview" })).toHaveAttribute(
      "src",
      "/api/v1/drafts/7f3a2cb2-a908-4216-84e4-7987127e7acc/preview.png",
    );
  });

  it("accepts a source image dropped on the upload zone", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        draft_id: "78f70bb3-acde-4ef8-a0a4-0cdbbe34ef8b",
        revision: 1,
        preview_url: "/api/v1/drafts/78f70bb3-acde-4ef8-a0a4-0cdbbe34ef8b/preview.png",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    const file = new File(["png"], "dropped-weather.png", { type: "image/png" });

    fireEvent.drop(screen.getByLabelText("Source image drop zone"), {
      dataTransfer: { files: [file] },
    });

    expect(await screen.findByRole("img", { name: "Spectra 6 preview" })).toBeInTheDocument();
    const submitted = fetchMock.mock.calls[0][1].body as FormData;
    expect(submitted.get("source")).toBe(file);
  });

  it("reprocesses the uploaded source when dithering changes", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          draft_id: "11111111-1111-4111-8111-111111111111",
          revision: 1,
          preview_url: "/api/v1/drafts/11111111-1111-4111-8111-111111111111/preview.png",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          draft_id: "11111111-1111-4111-8111-111111111111",
          revision: 2,
          preview_url: "/api/v1/drafts/11111111-1111-4111-8111-111111111111/preview.png?revision=2",
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.upload(
      screen.getByLabelText("Source image"),
      new File(["png"], "weather.png", { type: "image/png" }),
    );
    await screen.findByRole("img", { name: "Spectra 6 preview" });
    await userEvent.selectOptions(screen.getByLabelText("Dithering algorithm"), "none");

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/v1/drafts/11111111-1111-4111-8111-111111111111/preview",
    );
    const request = fetchMock.mock.calls[1][1];
    expect(request).toEqual(expect.objectContaining({
      method: "POST",
      headers: { "Content-Type": "application/json" },
    }));
    expect(JSON.parse(request.body as string).dithering).toBe("none");
    expect(JSON.parse(request.body as string).revision).toBe(2);
    expect(await screen.findByRole("img", { name: "Spectra 6 preview" })).toHaveAttribute(
      "src",
      "/api/v1/drafts/11111111-1111-4111-8111-111111111111/preview.png?revision=2",
    );
  });

  it("reprocesses the uploaded source when an image adjustment changes", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          draft_id: "33333333-3333-4333-8333-333333333333",
          revision: 1,
          preview_url: "/api/v1/drafts/33333333-3333-4333-8333-333333333333/preview.png",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          draft_id: "33333333-3333-4333-8333-333333333333",
          revision: 2,
          preview_url: "/api/v1/drafts/33333333-3333-4333-8333-333333333333/preview.png?revision=2",
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.upload(
      screen.getByLabelText("Source image"),
      new File(["png"], "weather.png", { type: "image/png" }),
    );
    await screen.findByRole("img", { name: "Spectra 6 preview" });
    fireEvent.change(screen.getByLabelText("Brightness"), { target: { value: "35" } });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body as string)).toEqual(expect.objectContaining({
      brightness: 35,
      contrast: 0,
      saturation: 0,
      gamma: 1,
      sharpness: 0,
    }));
  });

  it("cancels a queued preview when settings revert before debounce", async () => {
    const draftId = "99999999-9999-4999-8999-999999999999";
    const frameId = "99999999-9999-4999-8999-999999999998";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        json: async () => ({
          draft_id: draftId,
          revision: 1,
          preview_url: `/api/v1/drafts/${draftId}/revisions/1/preview.png`,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ draft_id: draftId, revision: 1, frame_id: frameId }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ current_frame_id: frameId, last_frame_id: frameId, is_current: true }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.upload(
      screen.getByLabelText("Source image"),
      new File(["png"], "weather.png", { type: "image/png" }),
    );
    const publish = await screen.findByRole("button", { name: "Send to frame now" });
    fireEvent.change(screen.getByLabelText("Brightness"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Brightness"), { target: { value: "0" } });
    await new Promise((resolve) => window.setTimeout(resolve, 400));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(publish).toBeEnabled();
    await userEvent.click(publish);
    await waitFor(() => expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      `/api/v1/drafts/${draftId}/publish`,
      expect.objectContaining({ method: "POST" }),
    ));
  });

  it("returns idle when an in-flight preview settles after settings revert", async () => {
    type DraftResponse = { ok: boolean; status: number; json: () => Promise<object> };
    const draftId = "abababab-abab-4bab-8bab-abababababab";
    const frameId = "cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd";
    let resolvePreview!: (value: DraftResponse) => void;
    const preview = new Promise<DraftResponse>((resolve) => { resolvePreview = resolve; });
    const initialPreviewUrl = `/api/v1/drafts/${draftId}/revisions/1/preview.png`;
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        json: async () => ({ draft_id: draftId, revision: 1, preview_url: initialPreviewUrl }),
      })
      .mockReturnValueOnce(preview)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ draft_id: draftId, revision: 2, frame_id: frameId }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ current_frame_id: frameId, last_frame_id: frameId, is_current: true }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.upload(
      screen.getByLabelText("Source image"),
      new File(["png"], "weather.png", { type: "image/png" }),
    );
    const image = await screen.findByRole("img", { name: "Spectra 6 preview" });
    fireEvent.change(screen.getByLabelText("Brightness"), { target: { value: "10" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    fireEvent.change(screen.getByLabelText("Brightness"), { target: { value: "0" } });

    resolvePreview({
      ok: true,
      status: 200,
      json: async () => ({
        draft_id: draftId,
        revision: 2,
        preview_url: `/api/v1/drafts/${draftId}/revisions/2/preview.png`,
      }),
    });

    const publish = screen.getByRole("button", { name: "Send to frame now" });
    await waitFor(() => expect(publish).toBeEnabled());
    expect(image).toHaveAttribute("src", initialPreviewUrl);
    await userEvent.click(publish);
    await waitFor(() => expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      `/api/v1/drafts/${draftId}/publish`,
      expect.objectContaining({ method: "POST" }),
    ));
  });

  it("returns idle when a stale in-flight preview fails after settings revert", async () => {
    type DraftResponse = { ok: boolean; status: number; json: () => Promise<object> };
    const draftId = "acacacac-acac-4cac-8cac-acacacacacac";
    const frameId = "dededede-dede-4ede-8ede-dededededede";
    let resolvePreview!: (value: DraftResponse) => void;
    const preview = new Promise<DraftResponse>((resolve) => { resolvePreview = resolve; });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        json: async () => ({
          draft_id: draftId,
          revision: 1,
          preview_url: `/api/v1/drafts/${draftId}/revisions/1/preview.png`,
        }),
      })
      .mockReturnValueOnce(preview)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ draft_id: draftId, revision: 2, frame_id: frameId }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ current_frame_id: frameId, last_frame_id: frameId, is_current: true }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.upload(
      screen.getByLabelText("Source image"),
      new File(["png"], "weather.png", { type: "image/png" }),
    );
    fireEvent.change(screen.getByLabelText("Brightness"), { target: { value: "10" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    fireEvent.change(screen.getByLabelText("Brightness"), { target: { value: "0" } });

    resolvePreview({ ok: false, status: 500, json: async () => ({}) });

    const publish = screen.getByRole("button", { name: "Send to frame now" });
    await waitFor(() => expect(publish).toBeEnabled());
    expect(screen.queryByText("Conversion failed")).not.toBeInTheDocument();
    await userEvent.click(publish);
    await waitFor(() => expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      `/api/v1/drafts/${draftId}/publish`,
      expect.objectContaining({ method: "POST" }),
    ));
  });

  it("coalesces slider changes to one latest preview while a render is in flight", async () => {
    let resolveOlder!: (value: { ok: boolean; status: number; json: () => Promise<object> }) => void;
    const older = new Promise<{ ok: boolean; status: number; json: () => Promise<object> }>((resolve) => {
      resolveOlder = resolve;
    });
    const draftId = "44444444-4444-4444-8444-444444444444";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        json: async () => ({
          draft_id: draftId,
          revision: 1,
          preview_url: `/api/v1/drafts/${draftId}/preview.png`,
        }),
      })
      .mockReturnValueOnce(older)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          draft_id: draftId,
          revision: 3,
          preview_url: `/api/v1/drafts/${draftId}/preview.png?revision=3`,
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.upload(
      screen.getByLabelText("Source image"),
      new File(["png"], "weather.png", { type: "image/png" }),
    );
    fireEvent.change(screen.getByLabelText("Brightness"), { target: { value: "10" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    fireEvent.change(screen.getByLabelText("Brightness"), { target: { value: "20" } });
    fireEvent.change(screen.getByLabelText("Brightness"), { target: { value: "30" } });
    await new Promise((resolve) => window.setTimeout(resolve, 400));
    expect(fetchMock).toHaveBeenCalledTimes(2);

    resolveOlder({
      ok: true,
      status: 200,
      json: async () => ({
        draft_id: draftId,
        revision: 2,
        preview_url: `/api/v1/drafts/${draftId}/preview.png?revision=2`,
      }),
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const latestBody = JSON.parse(fetchMock.mock.calls[2][1].body as string);
    expect(latestBody.brightness).toBe(30);
    expect(latestBody.revision).toBe(3);
    expect(await screen.findByRole("img", { name: "Spectra 6 preview" })).toHaveAttribute(
      "src",
      `/api/v1/drafts/${draftId}/preview.png?revision=3`,
    );
  });

  it("reconciles a superseded preview revision and retries current settings", async () => {
    const draftId = "77777777-7777-4777-8777-777777777777";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        json: async () => ({
          draft_id: draftId,
          revision: 1,
          preview_url: `/api/v1/drafts/${draftId}/revisions/1/preview.png`,
        }),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: async () => ({
          detail: { code: "preview_superseded", current_revision: 4, requested_revision: 5 },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          revision: 6,
          preview_url: `/api/v1/drafts/${draftId}/revisions/6/preview.png`,
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.upload(
      screen.getByLabelText("Source image"),
      new File(["png"], "weather.png", { type: "image/png" }),
    );
    fireEvent.change(screen.getByLabelText("Brightness"), { target: { value: "15" } });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(JSON.parse(fetchMock.mock.calls[2][1].body as string)).toEqual(
      expect.objectContaining({ revision: 6, brightness: 15 }),
    );
    expect(await screen.findByRole("img", { name: "Spectra 6 preview" })).toHaveAttribute(
      "src",
      `/api/v1/drafts/${draftId}/revisions/6/preview.png`,
    );
    expect(screen.getByRole("button", { name: "Send to frame now" })).toBeEnabled();
  });

  it("keeps publishing disabled when the latest preview fails", async () => {
    const draftId = "66666666-6666-4666-8666-666666666666";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        json: async () => ({
          draft_id: draftId,
          revision: 1,
          preview_url: `/api/v1/drafts/${draftId}/revisions/1/preview.png`,
        }),
      })
      .mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.upload(
      screen.getByLabelText("Source image"),
      new File(["png"], "weather.png", { type: "image/png" }),
    );
    fireEvent.change(screen.getByLabelText("Brightness"), { target: { value: "10" } });

    expect(await screen.findByText("Conversion failed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send to frame now" })).toBeDisabled();
  });

  it("keeps publishing disabled when an older preview finishes during a replacement upload", async () => {
    type DraftResponse = { ok: boolean; status: number; json: () => Promise<object> };
    const acceptedDraftId = "88888888-8888-4888-8888-888888888888";
    let resolvePreview!: (value: DraftResponse) => void;
    const preview = new Promise<DraftResponse>((resolve) => { resolvePreview = resolve; });
    const replacement = new Promise<DraftResponse>(() => {});
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        json: async () => ({
          draft_id: acceptedDraftId,
          revision: 1,
          preview_url: `/api/v1/drafts/${acceptedDraftId}/revisions/1/preview.png`,
        }),
      })
      .mockReturnValueOnce(preview)
      .mockReturnValueOnce(replacement);
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.upload(
      screen.getByLabelText("Source image"),
      new File(["accepted"], "accepted.png", { type: "image/png" }),
    );
    fireEvent.change(screen.getByLabelText("Brightness"), { target: { value: "10" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    fireEvent.drop(screen.getByLabelText("Source image drop zone"), {
      dataTransfer: { files: [new File(["replacement"], "replacement.png", { type: "image/png" })] },
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    resolvePreview({
      ok: true,
      status: 200,
      json: async () => ({
        draft_id: acceptedDraftId,
        revision: 2,
        preview_url: `/api/v1/drafts/${acceptedDraftId}/revisions/2/preview.png`,
      }),
    });

    await waitFor(() => expect(screen.getByRole("img", { name: "Spectra 6 preview" })).toHaveAttribute(
      "src",
      `/api/v1/drafts/${acceptedDraftId}/revisions/2/preview.png`,
    ));
    expect(screen.getByRole("button", { name: "Send to frame now" })).toBeDisabled();
  });

  it("keeps the newest source when overlapping uploads resolve out of order", async () => {
    type UploadResponse = { ok: boolean; status: number; json: () => Promise<object> };
    let resolveFirst!: (value: UploadResponse) => void;
    let resolveSecond!: (value: UploadResponse) => void;
    const first = new Promise<UploadResponse>((resolve) => { resolveFirst = resolve; });
    const second = new Promise<UploadResponse>((resolve) => { resolveSecond = resolve; });
    const fetchMock = vi.fn()
      .mockReturnValueOnce(first)
      .mockReturnValueOnce(second)
      .mockResolvedValue({ ok: true, status: 204 });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    const zone = screen.getByLabelText("Source image drop zone");

    fireEvent.drop(zone, {
      dataTransfer: { files: [new File(["first"], "first.png", { type: "image/png" })] },
    });
    fireEvent.drop(zone, {
      dataTransfer: { files: [new File(["second"], "second.png", { type: "image/png" })] },
    });
    resolveSecond({
      ok: true,
      status: 201,
      json: async () => ({
        draft_id: "22222222-2222-4222-8222-222222222222",
        revision: 1,
        preview_url: "/api/v1/drafts/22222222-2222-4222-8222-222222222222/revisions/1/preview.png",
      }),
    });
    await screen.findByRole("img", { name: "Spectra 6 preview" });
    resolveFirst({
      ok: true,
      status: 201,
      json: async () => ({
        draft_id: "11111111-1111-4111-8111-111111111111",
        revision: 1,
        preview_url: "/api/v1/drafts/11111111-1111-4111-8111-111111111111/revisions/1/preview.png",
      }),
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    expect(screen.getByRole("img", { name: "Spectra 6 preview" })).toHaveAttribute(
      "src",
      "/api/v1/drafts/22222222-2222-4222-8222-222222222222/revisions/1/preview.png",
    );
    expect(fetchMock.mock.calls[2][0]).toBe(
      "/api/v1/drafts/11111111-1111-4111-8111-111111111111",
    );
    expect(fetchMock.mock.calls[2][1]).toEqual({ method: "DELETE" });
  });

  it("deletes the accepted draft after the latest overlapping replacement succeeds", async () => {
    type UploadResponse = { ok: boolean; status: number; json: () => Promise<object> };
    const acceptedDraftId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const supersededDraftId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const replacementDraftId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    let resolveSuperseded!: (value: UploadResponse) => void;
    let resolveReplacement!: (value: UploadResponse) => void;
    const superseded = new Promise<UploadResponse>((resolve) => { resolveSuperseded = resolve; });
    const replacement = new Promise<UploadResponse>((resolve) => { resolveReplacement = resolve; });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        json: async () => ({
          draft_id: acceptedDraftId,
          revision: 1,
          preview_url: `/api/v1/drafts/${acceptedDraftId}/revisions/1/preview.png`,
        }),
      })
      .mockReturnValueOnce(superseded)
      .mockReturnValueOnce(replacement)
      .mockResolvedValue({ ok: true, status: 204 });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    const zone = screen.getByLabelText("Source image drop zone");

    fireEvent.drop(zone, {
      dataTransfer: { files: [new File(["accepted"], "accepted.png", { type: "image/png" })] },
    });
    await screen.findByRole("img", { name: "Spectra 6 preview" });
    fireEvent.drop(zone, {
      dataTransfer: { files: [new File(["superseded"], "superseded.png", { type: "image/png" })] },
    });
    fireEvent.drop(zone, {
      dataTransfer: { files: [new File(["replacement"], "replacement.png", { type: "image/png" })] },
    });

    resolveReplacement({
      ok: true,
      status: 201,
      json: async () => ({
        draft_id: replacementDraftId,
        revision: 1,
        preview_url: `/api/v1/drafts/${replacementDraftId}/revisions/1/preview.png`,
      }),
    });
    expect(await screen.findByRole("img", { name: "Spectra 6 preview" })).toHaveAttribute(
      "src",
      `/api/v1/drafts/${replacementDraftId}/revisions/1/preview.png`,
    );
    resolveSuperseded({
      ok: true,
      status: 201,
      json: async () => ({
        draft_id: supersededDraftId,
        revision: 1,
        preview_url: `/api/v1/drafts/${supersededDraftId}/revisions/1/preview.png`,
      }),
    });

    await waitFor(() => {
      const deletes = fetchMock.mock.calls.filter(([, options]) => options?.method === "DELETE");
      expect(deletes).toEqual(expect.arrayContaining([
        [`/api/v1/drafts/${acceptedDraftId}`, { method: "DELETE" }],
        [`/api/v1/drafts/${supersededDraftId}`, { method: "DELETE" }],
      ]));
      expect(deletes).toHaveLength(2);
    });
  });

  it("keeps a draft through a failed replacement and deletes it after a later success", async () => {
    const acceptedDraftId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
    const replacementDraftId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        json: async () => ({
          draft_id: acceptedDraftId,
          revision: 1,
          preview_url: `/api/v1/drafts/${acceptedDraftId}/revisions/1/preview.png`,
        }),
      })
      .mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({}) })
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        json: async () => ({
          draft_id: replacementDraftId,
          revision: 1,
          preview_url: `/api/v1/drafts/${replacementDraftId}/revisions/1/preview.png`,
        }),
      })
      .mockResolvedValueOnce({ ok: true, status: 204 });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    const zone = screen.getByLabelText("Source image drop zone");

    fireEvent.drop(zone, {
      dataTransfer: { files: [new File(["accepted"], "accepted.png", { type: "image/png" })] },
    });
    expect(await screen.findByRole("img", { name: "Spectra 6 preview" })).toHaveAttribute(
      "src",
      `/api/v1/drafts/${acceptedDraftId}/revisions/1/preview.png`,
    );
    fireEvent.drop(zone, {
      dataTransfer: { files: [new File(["failed"], "failed.png", { type: "image/png" })] },
    });
    await screen.findByText("Conversion failed");
    expect(screen.getByRole("img", { name: "Spectra 6 preview" })).toHaveAttribute(
      "src",
      `/api/v1/drafts/${acceptedDraftId}/revisions/1/preview.png`,
    );
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "DELETE")).toHaveLength(0);

    fireEvent.drop(zone, {
      dataTransfer: { files: [new File(["replacement"], "replacement.png", { type: "image/png" })] },
    });
    expect(await screen.findByRole("img", { name: "Spectra 6 preview" })).toHaveAttribute(
      "src",
      `/api/v1/drafts/${replacementDraftId}/revisions/1/preview.png`,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      `/api/v1/drafts/${acceptedDraftId}`,
      { method: "DELETE" },
    ));
  });

  it("publishes the current preview and waits for controller confirmation", async () => {
    const draftId = "55555555-5555-4555-8555-555555555554";
    const frameId = "55555555-5555-4555-8555-555555555555";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          draft_id: draftId,
          revision: 1,
          preview_url: `/api/v1/drafts/${draftId}/preview.png`,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ draft_id: draftId, revision: 1, frame_id: frameId, sequence: 99 }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ current_frame_id: frameId, last_frame_id: frameId, is_current: true }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.upload(
      screen.getByLabelText("Source image"),
      new File(["png"], "weather.png", { type: "image/png" }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Send to frame now" }));

    await screen.findByText("Controller confirmed");
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      `/api/v1/drafts/${draftId}/publish`,
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/v1/device/status",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("cancels Studio publish polling on History navigation and allows a fresh History publish", async () => {
    const draftId = "global-draft";
    let studioStatusSignal: AbortSignal | undefined;
    let statusRequests = 0;
    const pendingStatus = new Promise<Response>(() => undefined);
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (input === "/api/v1/drafts") return Promise.resolve({
        ok: true,
        status: 201,
        json: async () => ({ draft_id: draftId, revision: 1, preview_url: "/preview.png" }),
      } as Response);
      if (input === `/api/v1/drafts/${draftId}/publish`) return Promise.resolve({
        ok: true,
        json: async () => ({ frame_id: "studio-frame" }),
      } as Response);
      if (input === "/api/v1/device/status") {
        statusRequests += 1;
        if (statusRequests === 1) {
          studioStatusSignal = init?.signal ?? undefined;
          return pendingStatus;
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({
            is_current: true,
            last_frame_id: "history-frame",
            reported_at: "2026-08-24T08:00:01Z",
          }),
        } as Response);
      }
      if (input === "/api/v1/generations") return Promise.resolve({
        ok: true,
        json: async () => [{
          id: "history-frame",
          status: "published",
          planned_display_at: "2026-08-24T08:00:00Z",
          provider: "comfyui",
          model: "qwen-image-2512",
          frame_id: "history-frame",
          source: null,
        }],
      } as Response);
      if (input === "/api/v1/generations/history-frame/publish") return Promise.resolve({
        ok: true,
        json: async () => ({
          frame_id: "history-frame",
          already_current: true,
          activated_at: "2026-08-24T08:00:01Z",
        }),
      } as Response);
      throw new Error(`unexpected fetch ${String(input)}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.upload(
      screen.getByLabelText("Source image"),
      new File(["png"], "weather.png", { type: "image/png" }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Send to frame now" }));
    await waitFor(() => expect(studioStatusSignal).toBeDefined());

    await userEvent.click(screen.getByRole("button", { name: "History" }));

    expect(studioStatusSignal?.aborted).toBe(true);
    const historyPublish = await screen.findByRole("button", { name: "Send to frame now" });
    expect(historyPublish).toBeEnabled();
    expect(screen.queryByText("Controller confirmed")).not.toBeInTheDocument();
    await userEvent.click(historyPublish);
    expect(await screen.findByText("Shown on frame")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Studio" }));
    expect(screen.getByRole("button", { name: "Send to frame now" })).toBeEnabled();
    expect(screen.queryByText("Waiting for controller…")).not.toBeInTheDocument();
    expect(screen.queryByText("Publishing…")).not.toBeInTheDocument();
  });

  it("does not let an old Studio publish completion overwrite a newer operation", async () => {
    type ResponseStub = { ok: boolean; json: () => Promise<object> };
    const draftId = "token-guard-draft";
    let resolveOld!: (value: ResponseStub) => void;
    let resolveNew!: (value: ResponseStub) => void;
    const oldPublish = new Promise<ResponseStub>((resolve) => { resolveOld = resolve; });
    const newPublish = new Promise<ResponseStub>((resolve) => { resolveNew = resolve; });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ draft_id: draftId, revision: 1, preview_url: "/preview.png" }),
      })
      .mockReturnValueOnce(oldPublish)
      .mockReturnValueOnce(newPublish)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ is_current: true, last_frame_id: "new-frame" }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await userEvent.upload(
      screen.getByLabelText("Source image"),
      new File(["png"], "weather.png", { type: "image/png" }),
    );

    const send = await screen.findByRole("button", { name: "Send to frame now" });
    await userEvent.click(send);
    expect(await screen.findByRole("button", { name: "Publishing…" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Studio" }));
    await userEvent.click(screen.getByRole("button", { name: "Send to frame now" }));

    resolveOld({ ok: false, json: async () => ({}) });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(screen.getByRole("button", { name: "Publishing…" })).toBeDisabled();
    expect(screen.queryByText("Publish or confirmation failed")).not.toBeInTheDocument();

    resolveNew({ ok: true, json: async () => ({ frame_id: "new-frame" }) });
    expect(await screen.findByText("Controller confirmed")).toBeInTheDocument();
  });

  it("navigates between local-only Studio, Schedule and History sections", async () => {
    render(<App />);

    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    expect(screen.getByLabelText("Generator")).toHaveValue("qwen-image-2512");
    expect(screen.getByRole("option", { name: "Qwen-Image-2512" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "FLUX.2 Klein 4B" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Z-Image-Turbo" })).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/OpenAI|OAuth|API key|Secret reference/i);

    await userEvent.click(screen.getByRole("button", { name: "Schedule" }));
    expect(screen.getByRole("heading", { name: "Generation schedule" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "History" }));
    expect(screen.getByRole("heading", { name: "Generation history" })).toBeInTheDocument();
  });

  it("presents scheduling as cadence, quiet window and a 48-hour timeline", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        enabled: true,
        mode: "interval",
        interval_minutes: 120,
        anchor_time: "08:00",
        custom_times: [],
        quiet_start: "22:00",
        quiet_end: "07:00",
        next_runs: ["2026-08-25T08:00:00+03:00"],
      }),
    }));
    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Schedule" }));

    expect(await screen.findByRole("heading", { name: "Display cadence" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Cadence mode" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Every interval" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Specific times" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Quiet window" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "48-hour timeline" })).toBeInTheDocument();
    expect(screen.getByText("Generation starts 15 minutes before each display")).toBeInTheDocument();
  });

  it("shows dirty and saved states in the schedule action bar", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          enabled: true,
          mode: "interval",
          interval_minutes: 60,
          anchor_time: "08:00",
          custom_times: [],
          quiet_start: null,
          quiet_end: null,
          next_runs: [],
        }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Schedule" }));
    const save = await screen.findByRole("button", { name: "Save schedule" });
    expect(save).toBeDisabled();

    await userEvent.clear(screen.getByLabelText("Interval minutes"));
    await userEvent.type(screen.getByLabelText("Interval minutes"), "90");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    expect(save).toBeEnabled();

    await userEvent.click(save);
    expect(await screen.findByText("Schedule saved")).toBeInTheDocument();
    expect(save).toBeDisabled();
  });

  it("loads and saves an interval schedule with quiet hours", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          enabled: true,
          mode: "interval",
          interval_minutes: 90,
          anchor_time: "06:30",
          custom_times: [],
          quiet_start: "22:00",
          quiet_end: "07:00",
          next_runs: ["2026-08-24T06:15:00Z", "2026-08-24T07:45:00Z"],
        }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Schedule" }));
    expect(await screen.findByLabelText("Interval minutes")).toHaveValue(90);
    expect(screen.getByLabelText("Anchor time")).toHaveValue("06:30");
    expect(screen.getByText("Generation starts 15 minutes before each display")).toBeInTheDocument();
    expect(screen.getAllByText("09:15", { exact: false }).length).toBeGreaterThan(0);

    await userEvent.clear(screen.getByLabelText("Interval minutes"));
    await userEvent.type(screen.getByLabelText("Interval minutes"), "120");
    await userEvent.click(screen.getByRole("button", { name: "Save schedule" }));

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/v1/schedule");
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/v1/schedule", expect.objectContaining({
      method: "PUT",
      headers: { "Content-Type": "application/json" },
    }));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body as string)).toEqual({
      enabled: true,
      mode: "interval",
      interval_minutes: 120,
      anchor_time: "06:30",
      custom_times: [],
      quiet_start: "22:00",
      quiet_end: "07:00",
    });
  });

  it("adds and removes custom display times", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          enabled: true,
          mode: "custom",
          interval_minutes: 60,
          anchor_time: "08:00",
          custom_times: ["08:00", "18:30"],
          quiet_start: null,
          quiet_end: null,
          next_runs: [],
        }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Schedule" }));
    expect(await screen.findByLabelText("Custom time 1")).toHaveValue("08:00");
    await userEvent.click(screen.getByRole("button", { name: "Remove 08:00" }));
    await userEvent.click(screen.getByRole("button", { name: "Add time" }));

    expect(screen.queryByDisplayValue("08:00")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Custom time 1")).toHaveValue("18:30");
    expect(screen.getByLabelText("Custom time 2")).toHaveValue("12:00");

    await userEvent.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body as string).custom_times).toEqual(["18:30", "12:00"]);
  });

  it("renders generation history details, previews and errors", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            id: "gen-1",
            status: "published",
            planned_display_at: "2026-08-24T08:00:00Z",
            provider: "comfyui",
            model: "qwen-image-2512",
            generation_duration_seconds: 18.2,
            render_duration_seconds: 0.95,
            preview_url: "/api/v1/generations/gen-1/preview.png",
            frame_id: "frame-1",
            prompt_snapshot: { scene_id: "storm-coast", scene_family: "cinematic-weather" },
            source: {
              id: "source-1",
              url: "/api/v1/assets/source-1",
              download_url: "/api/v1/assets/source-1/download",
              media_type: "image/png",
              byte_length: 1234,
              sha256: "abc",
              width: 1024,
              height: 768,
            },
          },
          {
            id: "gen-2",
            status: "failed",
            planned_display_at: "2026-08-24T12:00:00Z",
            provider: "comfyui",
            model: "flux2-klein-4b",
            generation_duration_seconds: 4.2,
            render_duration_seconds: 0,
            error_detail: "Worker ran out of memory",
          },
        ],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByText("Qwen-Image-2512")).toBeInTheDocument();
    expect(screen.getByText("FLUX.2 Klein 4B")).toBeInTheDocument();
    expect(screen.getByText("published", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("failed", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("18.2s", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("Worker ran out of memory")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Model source for gen-1" })).toHaveAttribute("src", "/api/v1/assets/source-1");
    expect(screen.getByText("storm-coast")).toBeInTheDocument();
    expect(screen.getByText("cinematic-weather")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "Preview for gen-2" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/generations");
  });

  it("opens an accessible original-source dialog with download and all close interactions", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{
        id: "gen-modal",
        status: "published",
        planned_display_at: "2026-08-24T08:00:00Z",
        provider: "comfyui",
        model: "qwen-image-2512",
        frame_id: "frame-modal",
        preview_url: "/api/v1/frames/frame-modal/preview.png",
        source: {
          id: "source-modal",
          url: "/api/v1/assets/source-modal",
          download_url: "/api/v1/assets/source-modal/download",
          media_type: "image/png",
          byte_length: 1234,
          sha256: "abc",
          width: 1024,
          height: 768,
        },
      }],
    }));
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    const thumbnail = await screen.findByRole("button", { name: "Open original source for gen-modal" });

    await userEvent.click(thumbnail);
    let dialog = screen.getByRole("dialog", { name: "Original model source" });
    expect(dialog).toBeInTheDocument();
    const close = screen.getByRole("button", { name: "Close original source" });
    const download = screen.getByRole("link", { name: "Download original" });
    expect(close).toHaveFocus();
    const historyNav = screen.getByRole("button", { name: "History" });
    historyNav.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(close).toHaveFocus();
    historyNav.focus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(download).toHaveFocus();
    await userEvent.tab();
    expect(close).toHaveFocus();
    expect(screen.getByRole("img", { name: "Original model source for gen-modal" })).toHaveAttribute("src", "/api/v1/assets/source-modal");
    expect(screen.getByRole("link", { name: "Download original" })).toHaveAttribute("href", "/api/v1/assets/source-modal/download");
    expect(screen.getByRole("link", { name: "Download original" })).toHaveAttribute("download");
    expect(document.body.style.overflow).toBe("hidden");
    await userEvent.click(screen.getByRole("button", { name: "Close original source" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe("");
    expect(thumbnail).toHaveFocus();

    await userEvent.click(thumbnail);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await userEvent.click(thumbnail);
    dialog = screen.getByRole("dialog", { name: "Original model source" });
    fireEvent.mouseDown(dialog.parentElement!);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("falls back to the Spectra preview for history without a source", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{
        id: "gen-fallback",
        status: "published",
        planned_display_at: "2026-08-24T08:00:00Z",
        provider: "comfyui",
        model: "qwen-image-2512",
        frame_id: "frame-fallback",
        preview_url: "/api/v1/frames/frame-fallback/preview.png",
        source: null,
      }],
    }));
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByRole("img", { name: "Preview for gen-fallback" })).toHaveAttribute(
      "src",
      "/api/v1/frames/frame-fallback/preview.png",
    );
    expect(screen.queryByRole("button", { name: /Open original source/ })).not.toBeInTheDocument();
  });

  it("republishes a historical frame and waits for controller confirmation", async () => {
    type ResponseStub = { ok: boolean; json: () => Promise<object> };
    let resolvePublish!: (value: ResponseStub) => void;
    let resolveStatus!: (value: ResponseStub) => void;
    const publish = new Promise<ResponseStub>((resolve) => { resolvePublish = resolve; });
    const status = new Promise<ResponseStub>((resolve) => { resolveStatus = resolve; });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{
          id: "gen-send",
          status: "published",
          planned_display_at: "2026-08-24T08:00:00Z",
          provider: "comfyui",
          model: "qwen-image-2512",
          frame_id: "frame-send",
          preview_url: "/api/v1/frames/frame-send/preview.png",
          source: null,
        }],
      })
      .mockReturnValueOnce(publish)
      .mockReturnValueOnce(status);
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    const send = await screen.findByRole("button", { name: "Send to frame now" });

    await userEvent.click(send);
    expect(send).toBeDisabled();
    expect(screen.getByText("Publishing…")).toBeInTheDocument();
    fireEvent.click(send);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    resolvePublish({
      ok: true,
      json: async () => ({
        frame_id: "frame-send",
        already_current: false,
        activated_at: "2026-08-24T08:00:01Z",
      }),
    });
    expect(await screen.findByText("Waiting for frame…")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/v1/generations/gen-send/publish",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/v1/device/status",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    resolveStatus({
      ok: true,
      json: async () => ({
        is_current: true,
        last_frame_id: "frame-send",
        reported_at: "2026-08-24T08:00:02Z",
      }),
    });
    expect(await screen.findByText("Shown on frame")).toBeInTheDocument();
  });

  it("confirms an already-current historical frame only after a fresh matching report", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{
          id: "gen-current",
          status: "published",
          planned_display_at: "2026-08-24T08:00:00Z",
          provider: "comfyui",
          model: "qwen-image-2512",
          frame_id: "frame-current",
          source: null,
        }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          frame_id: "frame-current",
          already_current: true,
          activated_at: "2026-08-24T08:00:01Z",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          is_current: true,
          last_frame_id: "frame-current",
          reported_at: "2026-08-24T08:00:01Z",
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "History" }));

    await userEvent.click(await screen.findByRole("button", { name: "Send to frame now" }));

    expect(await screen.findByText("Shown on frame")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/v1/device/status",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it.each([
    ["stale", { is_current: true, last_frame_id: "frame-current", reported_at: "2026-08-24T08:00:00Z" }],
    ["mismatched", { is_current: true, last_frame_id: "other-frame", reported_at: "2026-08-24T08:00:02Z" }],
  ])("keeps waiting for an already-current frame with a %s report", async (_label, device) => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{
          id: "gen-current",
          status: "published",
          planned_display_at: "2026-08-24T08:00:00Z",
          provider: "comfyui",
          model: "qwen-image-2512",
          frame_id: "frame-current",
          source: null,
        }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          frame_id: "frame-current",
          already_current: true,
          activated_at: "2026-08-24T08:00:01Z",
        }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => device });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "History" }));

    await userEvent.click(await screen.findByRole("button", { name: "Send to frame now" }));

    expect(await screen.findByText("Waiting for frame…")).toBeInTheDocument();
    expect(screen.queryByText("Shown on frame")).not.toBeInTheDocument();
  });

  it("shows an error instead of confirming an already-current frame while offline", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{
          id: "gen-offline",
          status: "published",
          planned_display_at: "2026-08-24T08:00:00Z",
          provider: "comfyui",
          model: "qwen-image-2512",
          frame_id: "frame-offline",
          source: null,
        }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          frame_id: "frame-offline",
          already_current: true,
          activated_at: "2026-08-24T08:00:01Z",
        }),
      })
      .mockResolvedValueOnce({ ok: false, status: 503 });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "History" }));

    await userEvent.click(await screen.findByRole("button", { name: "Send to frame now" }));

    expect(await screen.findByText("Send failed")).toBeInTheDocument();
    expect(screen.queryByText("Shown on frame")).not.toBeInTheDocument();
  });

  it("globally disables historical publish actions while one is active", async () => {
    const pending = new Promise<Response>(() => undefined);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ["one", "two"].map((id) => ({
          id,
          status: "published",
          planned_display_at: "2026-08-24T08:00:00Z",
          provider: "comfyui",
          model: "qwen-image-2512",
          frame_id: `frame-${id}`,
          source: null,
        })),
      })
      .mockReturnValueOnce(pending);
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    const actions = await screen.findAllByRole("button", { name: "Send to frame now" });

    await userEvent.click(actions[0]);

    expect(actions[0]).toBeDisabled();
    expect(actions[1]).toBeDisabled();
    await userEvent.click(actions[1]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("aborts already-current confirmation polling when leaving History", async () => {
    let statusSignal: AbortSignal | undefined;
    const status = new Promise<Response>(() => undefined);
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (input === "/api/v1/generations") {
        return Promise.resolve({
          ok: true,
          json: async () => [{
            id: "gen-abort",
            status: "published",
            planned_display_at: "2026-08-24T08:00:00Z",
            provider: "comfyui",
            model: "qwen-image-2512",
            frame_id: "frame-abort",
            source: null,
          }],
        } as Response);
      }
      if (input === "/api/v1/generations/gen-abort/publish") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            frame_id: "frame-abort",
            already_current: true,
            activated_at: "2026-08-24T08:00:01Z",
          }),
        } as Response);
      }
      statusSignal = init?.signal ?? undefined;
      return status;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    await userEvent.click(await screen.findByRole("button", { name: "Send to frame now" }));
    expect(await screen.findByText("Waiting for frame…")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Studio" }));

    expect(statusSignal?.aborted).toBe(true);
    expect(screen.queryByText("Waiting for frame…")).not.toBeInTheDocument();
  });

  it("clears a History row when the active tab is clicked and after navigation", async () => {
    const pendingStatus = new Promise<Response>(() => undefined);
    const generation = {
      id: "gen-clear",
      status: "published",
      planned_display_at: "2026-08-24T08:00:00Z",
      provider: "comfyui",
      model: "qwen-image-2512",
      frame_id: "frame-clear",
      source: null,
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (input === "/api/v1/generations") return Promise.resolve({
        ok: true,
        json: async () => [generation],
      } as Response);
      if (input === "/api/v1/generations/gen-clear/publish") return Promise.resolve({
        ok: true,
        json: async () => ({
          frame_id: "frame-clear",
          already_current: false,
          activated_at: "2026-08-24T08:00:01Z",
        }),
      } as Response);
      if (input === "/api/v1/device/status") return pendingStatus;
      throw new Error(`unexpected fetch ${String(input)}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    await userEvent.click(await screen.findByRole("button", { name: "Send to frame now" }));
    expect(await screen.findByText("Waiting for frame…")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "History" }));
    expect(screen.queryByText("Waiting for frame…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send to frame now" })).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: "Studio" }));
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByRole("button", { name: "Send to frame now" })).toBeEnabled();
    expect(screen.queryByText("Publishing…")).not.toBeInTheDocument();
    expect(screen.queryByText("Waiting for frame…")).not.toBeInTheDocument();
  });

  it("cancels a historical publish retry timer on page switch", async () => {
    const clearTimeoutSpy = vi.spyOn(window, "clearTimeout");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{
          id: "gen-timer",
          status: "published",
          planned_display_at: "2026-08-24T08:00:00Z",
          provider: "comfyui",
          model: "qwen-image-2512",
          frame_id: "frame-timer",
          source: null,
        }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          frame_id: "frame-timer",
          already_current: false,
          activated_at: "2026-08-24T08:00:02Z",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          is_current: true,
          last_frame_id: "frame-timer",
          reported_at: "2026-08-24T08:00:01Z",
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    await userEvent.click(await screen.findByRole("button", { name: "Send to frame now" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    await userEvent.click(screen.getByRole("button", { name: "Studio" }));

    expect(clearTimeoutSpy).toHaveBeenCalled();
  });

  it("shows per-row republish errors and omits the action without a frame", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            id: "gen-error",
            status: "published",
            planned_display_at: "2026-08-24T08:00:00Z",
            provider: "comfyui",
            model: "qwen-image-2512",
            frame_id: "frame-error",
            preview_url: "/api/v1/frames/frame-error/preview.png",
            source: null,
          },
          {
            id: "gen-no-frame",
            status: "failed",
            planned_display_at: "2026-08-24T09:00:00Z",
            provider: "comfyui",
            model: "qwen-image-2512",
            frame_id: null,
            source: null,
          },
        ],
      })
      .mockResolvedValueOnce({ ok: false, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    const send = await screen.findByRole("button", { name: "Send to frame now" });
    expect(screen.getAllByRole("button", { name: "Send to frame now" })).toHaveLength(1);
    await userEvent.click(send);
    expect(await screen.findByText("Send failed")).toBeInTheDocument();
    expect(send).toBeEnabled();
  });

  it("shows history loading and empty states", async () => {
    let resolveRequest!: (value: { ok: boolean; json: () => Promise<{ items: never[] }> }) => void;
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise((resolve) => { resolveRequest = resolve; })));
    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "History" }));
    expect(screen.getByText("Loading generation history…")).toBeInTheDocument();
    resolveRequest({ ok: true, json: async () => ({ items: [] }) });
    expect(await screen.findByText("No generations yet")).toBeInTheDocument();
  });

  it("shows the history API error state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByText("Unable to load generation history.")).toBeInTheDocument();
  });

  it("saves the current processing settings as a default preset", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: "p1" }) });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.clear(screen.getByLabelText("Preset name"));
    await userEvent.type(screen.getByLabelText("Preset name"), "Outdoor");
    await userEvent.click(screen.getByRole("button", { name: "Save preset" }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/presets",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );
    const request = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(request.name).toBe("Outdoor");
    expect(request.make_default).toBe(true);
    expect(request.settings.dithering).toBe("good-display-floyd-v1");
    expect(request.settings.gamma).toBe(1);
  });
});
