/**
 * Hex parsing, sanitizing and the clipboard — carried from blobert's
 * `utils/clipboardHex.test.ts` and `clipboardHex.adversarial.test.ts`.
 *
 * The two source files are below unchanged except for the import path and the
 * rename `normalizeHexForBuildOption` → `normalizeHex`. The block at the end is
 * new: the defect the extraction exposed, each case failing against the source.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  copyHexToClipboard,
  findHexInText,
  hexForColorInput,
  normalizeHex,
  readHexFromClipboard,
  sanitizeHex,
} from "../src/controls/colorHex";

describe("normalizeHex", () => {
  it("accepts #RRGGBB and RRGGBB", () => {
    expect(normalizeHex("#aABBcc")).toBe("aabbcc");
    expect(normalizeHex("00ff00")).toBe("00ff00");
  });

  it("trims and rejects invalid", () => {
    expect(normalizeHex("  #010203  ")).toBe("010203");
    expect(normalizeHex("bad")).toBeNull();
    expect(normalizeHex("#fff")).toBeNull();
  });
});

describe("clipboard round-trip", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("copy writes #form; read returns 6-char form", async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    const read = vi.fn().mockResolvedValue("#c0ffee");
    vi.stubGlobal("navigator", {
      clipboard: { writeText: write, readText: read },
    });

    await expect(copyHexToClipboard("c0ffee")).resolves.toBe(true);
    expect(write).toHaveBeenCalledWith("#c0ffee");

    await expect(readHexFromClipboard()).resolves.toBe("c0ffee");
    read.mockResolvedValue("#bada55");
    await expect(readHexFromClipboard()).resolves.toBe("bada55");
  });
});


/**
 * ADVERSARIAL TEST SUITE: Hex Value Parsing & Normalization
 *
 * This test suite exposes weaknesses in hex validation, normalization, and
 * clipboard I/O error handling. Tests include:
 * - Null/undefined/empty clipboard content
 * - Malformed hex strings (wrong length, invalid chars, Unicode)
 * - Case sensitivity and normalization
 * - Whitespace handling
 * - Clipboard API failures and timeouts
 * - Concurrent clipboard operations
 */

describe("normalizeHex — Input Mutations", () => {
  describe("Valid hex inputs", () => {
    it("accepts lowercase #RRGGBB", () => {
      expect(normalizeHex("#aabbcc")).toBe("aabbcc");
    });

    it("accepts uppercase #RRGGBB", () => {
      expect(normalizeHex("#AABBCC")).toBe("aabbcc"); // Normalized to lowercase
    });

    it("accepts mixed case #RrGgBb", () => {
      expect(normalizeHex("#AaBbCc")).toBe("aabbcc");
    });

    it("accepts RRGGBB without #", () => {
      expect(normalizeHex("ff0000")).toBe("ff0000");
    });

    it("accepts 000000 (black)", () => {
      expect(normalizeHex("000000")).toBe("000000");
    });

    it("accepts ffffff (white)", () => {
      expect(normalizeHex("ffffff")).toBe("ffffff");
    });

    it("accepts with leading/trailing whitespace: '  #ff0000  '", () => {
      expect(normalizeHex("  #ff0000  ")).toBe("ff0000");
    });

    it("accepts newline and tab whitespace: '\\t#ff0000\\n'", () => {
      expect(normalizeHex("\t#ff0000\n")).toBe("ff0000");
    });
  });

  describe("Invalid hex inputs", () => {
    it("rejects empty string", () => {
      expect(normalizeHex("")).toBeNull();
    });

    it("rejects whitespace-only string", () => {
      expect(normalizeHex("   ")).toBeNull();
    });

    it("rejects null", () => {
      // TypeScript would prevent this, but runtime test it
      expect(normalizeHex(null as any)).toBeNull();
    });

    it("rejects undefined", () => {
      expect(normalizeHex(undefined as any)).toBeNull();
    });

    it("rejects too-short hex: '#fff' (3 chars, not 6)", () => {
      expect(normalizeHex("#fff")).toBeNull();
    });

    it("rejects too-long hex: '#ff0000ff' (8 chars, RGBA)", () => {
      expect(normalizeHex("#ff0000ff")).toBeNull();
    });

    it("rejects non-hex chars: '#gggggg'", () => {
      expect(normalizeHex("#gggggg")).toBeNull();
    });

    it("rejects partial non-hex: '#ffgg00'", () => {
      expect(normalizeHex("#ffgg00")).toBeNull();
    });

    it("rejects #-only: '#'", () => {
      expect(normalizeHex("#")).toBeNull();
    });

    it("rejects text mixed with hex: 'red #ff0000'", () => {
      expect(normalizeHex("red #ff0000")).toBeNull();
    });

    it("rejects single hex digit: '5'", () => {
      expect(normalizeHex("5")).toBeNull();
    });

    it("rejects 4-char hex: '#ff00'", () => {
      expect(normalizeHex("#ff00")).toBeNull();
    });

    it("rejects 5-char hex: '#ff000'", () => {
      expect(normalizeHex("#ff000")).toBeNull();
    });

    it("rejects 7-char hex: '#ff00000'", () => {
      expect(normalizeHex("#ff00000")).toBeNull();
    });
  });

  describe("Edge cases: whitespace and special chars", () => {
    it("accepts #ff0000 with leading/trailing spaces", () => {
      expect(normalizeHex("  #ff0000  ")).toBe("ff0000");
    });

    it("accepts ff0000 with leading/trailing spaces (no #)", () => {
      expect(normalizeHex("  ff0000  ")).toBe("ff0000");
    });

    it("rejects hex with internal whitespace: '#ff 00 00'", () => {
      expect(normalizeHex("#ff 00 00")).toBeNull();
    });

    it("rejects hex with newline: '#ff0000\\n00'", () => {
      expect(normalizeHex("#ff0000\n00")).toBeNull();
    });

    it("rejects hex with null byte: '#ff0000\\0'", () => {
      expect(normalizeHex("#ff0000\0ff")).toBeNull();
    });

    it("rejects non-ASCII chars: '#ff00é0'", () => {
      expect(normalizeHex("#ff00é0")).toBeNull();
    });

    it("rejects emoji: '🔴' (red circle emoji)", () => {
      expect(normalizeHex("🔴")).toBeNull();
    });

    it("rejects hex with plus sign: '#+ff0000'", () => {
      expect(normalizeHex("#+ff0000")).toBeNull();
    });

    it("rejects hex with minus sign: '#-ff0000'", () => {
      expect(normalizeHex("#-ff0000")).toBeNull();
    });

    it("rejects hex with x prefix: '#x0000ff'", () => {
      expect(normalizeHex("#x0000ff")).toBeNull();
    });

    it("rejects 0x prefix (C-style): '0xff0000'", () => {
      expect(normalizeHex("0xff0000")).toBeNull();
    });
  });

  describe("Edge cases: very long inputs", () => {
    it("rejects extremely long string", () => {
      const longString = "#" + "ff".repeat(1000);
      expect(normalizeHex(longString)).toBeNull();
    });

    it("rejects 1MB+ string (stress test)", () => {
      const veryLong = "a".repeat(1024 * 1024);
      expect(() => normalizeHex(veryLong)).not.toThrow();
      expect(normalizeHex(veryLong)).toBeNull();
      // CHECKPOINT: Should handle large inputs without hanging or crash
    });
  });

  describe("Edge cases: case insensitivity", () => {
    it("converts uppercase to lowercase: AABBCC → aabbcc", () => {
      expect(normalizeHex("AABBCC")).toBe("aabbcc");
    });

    it("converts mixed case to lowercase: AaBbCc → aabbcc", () => {
      expect(normalizeHex("AaBbCc")).toBe("aabbcc");
    });

    it("preserves lowercase: aabbcc → aabbcc", () => {
      expect(normalizeHex("aabbcc")).toBe("aabbcc");
    });

    it("uppercase with #: #AABBCC → aabbcc", () => {
      expect(normalizeHex("#AABBCC")).toBe("aabbcc");
    });
  });
});

describe("readHexFromClipboard — Clipboard API Edge Cases", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe("Successful reads", () => {
    it("reads #RRGGBB and returns 6-char normalized form", async () => {
      const read = vi.fn().mockResolvedValue("#ff0000");
      vi.stubGlobal("navigator", {
        clipboard: { readText: read, writeText: vi.fn() },
      });

      const result = await readHexFromClipboard();
      expect(result).toBe("ff0000");
    });

    it("reads RRGGBB (no #) and returns normalized form", async () => {
      const read = vi.fn().mockResolvedValue("00ff00");
      vi.stubGlobal("navigator", {
        clipboard: { readText: read, writeText: vi.fn() },
      });

      const result = await readHexFromClipboard();
      expect(result).toBe("00ff00");
    });

    it("reads uppercase and normalizes to lowercase", async () => {
      const read = vi.fn().mockResolvedValue("#AABBCC");
      vi.stubGlobal("navigator", {
        clipboard: { readText: read, writeText: vi.fn() },
      });

      const result = await readHexFromClipboard();
      expect(result).toBe("aabbcc");
    });
  });

  describe("Invalid clipboard content", () => {
    it("returns null for empty clipboard", async () => {
      const read = vi.fn().mockResolvedValue("");
      vi.stubGlobal("navigator", {
        clipboard: { readText: read, writeText: vi.fn() },
      });

      const result = await readHexFromClipboard();
      expect(result).toBeNull();
    });

    it("returns null for non-hex text in clipboard", async () => {
      const read = vi.fn().mockResolvedValue("not a hex value");
      vi.stubGlobal("navigator", {
        clipboard: { readText: read, writeText: vi.fn() },
      });

      const result = await readHexFromClipboard();
      expect(result).toBeNull();
    });

    it("returns null for partial hex (too short)", async () => {
      const read = vi.fn().mockResolvedValue("#fff");
      vi.stubGlobal("navigator", {
        clipboard: { readText: read, writeText: vi.fn() },
      });

      const result = await readHexFromClipboard();
      expect(result).toBeNull();
    });

    it("extracts RGB from oversized hex string (RGBA)", async () => {
      const read = vi.fn().mockResolvedValue("#ff0000ff"); // RGBA, extract RGB
      vi.stubGlobal("navigator", {
        clipboard: { readText: read, writeText: vi.fn() },
      });

      const result = await readHexFromClipboard();
      expect(result).toBe("ff0000"); // Extracts first 6 hex digits
    });

    it("returns null for #-only clipboard", async () => {
      const read = vi.fn().mockResolvedValue("#");
      vi.stubGlobal("navigator", {
        clipboard: { readText: read, writeText: vi.fn() },
      });

      const result = await readHexFromClipboard();
      expect(result).toBeNull();
    });

    it("extracts hex from text with emoji prefix", async () => {
      const read = vi.fn().mockResolvedValue("🎨 #ff0000");
      vi.stubGlobal("navigator", {
        clipboard: { readText: read, writeText: vi.fn() },
      });

      const result = await readHexFromClipboard();
      expect(result).toBe("ff0000"); // Extracts hex pattern despite emoji prefix
      // CHECKPOINT: Lenient parsing extracts hex from mixed content
    });

    it("returns null for very long clipboard content", async () => {
      const longText = "a".repeat(1024 * 1024);
      const read = vi.fn().mockResolvedValue(longText);
      vi.stubGlobal("navigator", {
        clipboard: { readText: read, writeText: vi.fn() },
      });

      const result = await readHexFromClipboard();
      expect(result).toBeNull();
      // CHECKPOINT: Large clipboard content is handled
    });

    it("handles whitespace-padded hex", async () => {
      const read = vi.fn().mockResolvedValue("  #ff0000  ");
      vi.stubGlobal("navigator", {
        clipboard: { readText: read, writeText: vi.fn() },
      });

      const result = await readHexFromClipboard();
      expect(result).toBe("ff0000");
    });
  });

  describe("Clipboard API failures", () => {
    it("handles readText rejection (Permission denied)", async () => {
      const read = vi.fn().mockRejectedValue(new Error("NotAllowedError"));
      vi.stubGlobal("navigator", {
        clipboard: { readText: read, writeText: vi.fn() },
      });

      const result = await readHexFromClipboard();
      expect(result).toBeNull();
      // CHECKPOINT: Promise rejection is caught, returns null
    });

    it("handles readText timeout (no clipboard available)", async () => {
      const read = vi.fn().mockImplementation(
        () => new Promise((resolve) => setTimeout(() => resolve(""), 10000)),
      );
      vi.stubGlobal("navigator", {
        clipboard: { readText: read, writeText: vi.fn() },
      });

      void readHexFromClipboard(); // was `const promise =`, unused; this tsconfig rejects unused locals
      // Don't actually wait 10s; just verify function doesn't hang indefinitely
      vi.useFakeTimers();
      expect(() => vi.advanceTimersByTime(1000)).not.toThrow();
      vi.useRealTimers();
    });

    it("handles missing navigator.clipboard", async () => {
      vi.stubGlobal("navigator", {});

      const result = await readHexFromClipboard();
      expect(result).toBeNull();
      // CHECKPOINT: Missing clipboard API is handled gracefully
    });

    it("handles null navigator", async () => {
      vi.stubGlobal("navigator", null as any);

      const result = await readHexFromClipboard();
      expect(result).toBeNull();
    });

    it("handles navigator.clipboard.readText as null", async () => {
      vi.stubGlobal("navigator", {
        clipboard: { readText: null as any, writeText: vi.fn() },
      });

      const result = await readHexFromClipboard();
      expect(result).toBeNull();
    });
  });

  describe("Concurrency and race conditions", () => {
    it("multiple readHexFromClipboard calls in parallel", async () => {
      const read = vi.fn()
        .mockResolvedValueOnce("#ff0000")
        .mockResolvedValueOnce("#00ff00")
        .mockResolvedValueOnce("#0000ff");

      vi.stubGlobal("navigator", {
        clipboard: { readText: read, writeText: vi.fn() },
      });

      const results = await Promise.all([
        readHexFromClipboard(),
        readHexFromClipboard(),
        readHexFromClipboard(),
      ]);

      expect(results).toEqual(["ff0000", "00ff00", "0000ff"]);
      expect(read).toHaveBeenCalledTimes(3);
      // CHECKPOINT: Concurrent reads work correctly
    });

    it("clipboard content changes between reads", async () => {
      const read = vi.fn()
        .mockResolvedValueOnce("#ff0000")
        .mockResolvedValueOnce("#00ff00");

      vi.stubGlobal("navigator", {
        clipboard: { readText: read, writeText: vi.fn() },
      });

      const result1 = await readHexFromClipboard();
      const result2 = await readHexFromClipboard();

      expect(result1).toBe("ff0000");
      expect(result2).toBe("00ff00");
      // CHECKPOINT: Sequential reads capture actual clipboard state
    });
  });
});

describe("copyHexToClipboard — Write Operations", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe("Successful writes", () => {
    it("writes #RRGGBB format to clipboard", async () => {
      const write = vi.fn().mockResolvedValue(undefined);
      vi.stubGlobal("navigator", {
        clipboard: { readText: vi.fn(), writeText: write },
      });

      const result = await copyHexToClipboard("ff0000");
      expect(result).toBe(true);
      expect(write).toHaveBeenCalledWith("#ff0000");
    });

    it("normalizes uppercase input before writing", async () => {
      const write = vi.fn().mockResolvedValue(undefined);
      vi.stubGlobal("navigator", {
        clipboard: { readText: vi.fn(), writeText: write },
      });

      const result = await copyHexToClipboard("AABBCC");
      expect(result).toBe(true);
      expect(write).toHaveBeenCalledWith("#aabbcc");
      // CHECKPOINT: Input is normalized before write
    });
  });

  describe("Write failures", () => {
    it("returns false on permission denied", async () => {
      const write = vi.fn().mockRejectedValue(new Error("NotAllowedError"));
      vi.stubGlobal("navigator", {
        clipboard: { readText: vi.fn(), writeText: write },
      });

      const result = await copyHexToClipboard("ff0000");
      expect(result).toBe(false);
    });

    it("returns false if clipboard API missing", async () => {
      vi.stubGlobal("navigator", {});

      const result = await copyHexToClipboard("ff0000");
      expect(result).toBe(false);
    });

    it("returns false on unexpected error", async () => {
      const write = vi.fn().mockRejectedValue(new Error("Unknown error"));
      vi.stubGlobal("navigator", {
        clipboard: { readText: vi.fn(), writeText: write },
      });

      const result = await copyHexToClipboard("ff0000");
      expect(result).toBe(false);
    });
  });
});

describe("hexForColorInput — Formatting Utility", () => {
  it("converts 6-char hex to #RRGGBB", () => {
    expect(hexForColorInput("ff0000")).toBe("#ff0000");
  });

  it("converts uppercase hex to #rrggbb (lowercase)", () => {
    expect(hexForColorInput("FF0000")).toBe("#ff0000");
  });

  it("handles empty string", () => {
    const result = hexForColorInput("");
    expect(result).toBe("#6b6b6b"); // Falls back to neutral gray
    // CHECKPOINT: Empty input falls back to valid default color
  });

  it("handles null/undefined", () => {
    expect(() => hexForColorInput(null as any)).not.toThrow();
    expect(() => hexForColorInput(undefined as any)).not.toThrow();
    // CHECKPOINT: Graceful handling of invalid input
  });
});

describe("sanitizeHex — Stripping Invalid Characters", () => {
  it("removes non-hex characters", () => {
    const result = sanitizeHex("ff00ggbb");
    expect(result).toBe("ff00bb"); // 'gg' removed
    // CHECKPOINT: Character filtering works
  });

  it("removes whitespace", () => {
    const result = sanitizeHex("ff 00 00");
    expect(result).toBe("ff0000");
  });

  it("removes # prefix", () => {
    const result = sanitizeHex("#ff0000");
    expect(result).toBe("ff0000");
  });

  it("handles completely invalid input", () => {
    const result = sanitizeHex("xyz");
    expect(result).toBe(""); // All chars filtered
  });
});

/**
 * New here. The source's last resort for clipboard text stripped every non-hex
 * character and accepted any six digits left over, so text that was never a
 * colour became one. Each case below returned a colour from the source's
 * `readHexFromClipboard`.
 */
describe("readHexFromClipboard — does not invent a colour from scattered digits", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const clipboardHolding = (text: string): void => {
    vi.stubGlobal("navigator", {
      clipboard: { readText: vi.fn().mockResolvedValue(text), writeText: vi.fn() },
    });
  };

  it.each([
    ["an rgb triple", "12, 34, 56"],
    ["a clock time", "12:34:56"],
    ["letters spread across words", "a b c d e f"],
  ])("returns null for %s", async (_what, text) => {
    // Source: "123456", "123456" and "abcdef".
    clipboardHolding(text);
    await expect(readHexFromClipboard()).resolves.toBeNull();
  });

  it("still recovers a standalone hex from quotes or CSS", async () => {
    clipboardHolding('"ff0000"');
    await expect(readHexFromClipboard()).resolves.toBe("ff0000");
    clipboardHolding("color: #00FF00;");
    await expect(readHexFromClipboard()).resolves.toBe("00ff00");
  });
});

describe("findHexInText", () => {
  it("prefers a #-prefixed code to a bare run", () => {
    expect(findHexInText("abcdef then #123456")).toBe("123456");
  });

  it("rejects a run longer than six digits", () => {
    expect(findHexInText("ff0000ff")).toBeNull();
  });
});

/** Review findings: recovery took six digits from a longer run, or from prose. */
describe("findHexInText — takes only a colour that was written as one", () => {
  it.each([
    ["a #-run of ten", "#deadbeef00"],
    ["a #-run of seven", "#ff00001"],
    ["a six-letter word in prose", "a decade ago"],
    ["a six-digit number in prose", "order 123456 shipped"],
    ["an unmatched quote", "\"ff0000'"],
  ])("returns null for %s", (_what, text) => {
    expect(findHexInText(text)).toBeNull();
  });

  it.each([
    ["#rrggbb", "#FF0000", "ff0000"],
    ["#rrggbbaa, as its RGB", "#ff000080", "ff0000"],
    ["a quoted run", "'00ff00'", "00ff00"],
    ["a quoted run with spaces", "  \"00ff00\"  ", "00ff00"],
    ["a #-run later in the text", "#ff00001 or #00ff00", "00ff00"],
  ])("recovers %s", (_what, text, hex) => {
    expect(findHexInText(text)).toBe(hex);
  });
});
