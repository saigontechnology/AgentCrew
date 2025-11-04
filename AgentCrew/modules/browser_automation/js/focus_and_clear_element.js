/**
 * Focus the target element and clear any existing content.
 *
 * @param {string} xpath - XPath selector for the element
 * @returns {Object} Result object with success status and message
 */
function focusAndClearElement(xpath) {
  const result = document.evaluate(
    xpath,
    document,
    null,
    XPathResult.FIRST_ORDERED_NODE_TYPE,
    null,
  );
  const element = result.singleNodeValue;

  if (!element) {
    return { success: false, error: "Element not found" };
  }

  // Check if element is visible and enabled
  const style = window.getComputedStyle(element);
  if (style.display === "none" || style.visibility === "hidden") {
    return { success: false, error: "Element is not visible" };
  }

  if (element.disabled) {
    return { success: false, error: "Element is disabled" };
  }

  // Check if element is a valid input type
  const tagName = element.tagName.toLowerCase();

  // Scroll element into view and focus
  element.scrollIntoView({ behavior: "instant", block: "center" });
  element.focus();

  if (
    !["input", "textarea"].includes(tagName) &&
    !element.hasAttribute("contenteditable")
  ) {
    return {
      success: true,
      canSimulateTyping: false,
      message: "Element focused but not an input, textarea, or contenteditable",
    };
  }

  // Clear existing content - select all and then we'll type over it
  if (tagName === "input" || tagName === "textarea") {
    element.select();
  } else if (element.hasAttribute("contenteditable")) {
    // For contenteditable, select all text
    const range = document.createRange();
    range.selectNodeContents(element);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  return {
    success: true,
    canSimulateTyping: true,
    message: "Element focused and selected for typing",
  };
}

// Export the function - when used in browser automation, wrap with IIFE and pass xpath
// (() => {
//     const xpath = '{XPATH_PLACEHOLDER}';
//     return focusAndClearElement(xpath);
// })();
