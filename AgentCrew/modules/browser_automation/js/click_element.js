/**
 * Calculate click coordinates for an element using XPath selector.
 * Returns coordinates relative to the main frame's viewport in CSS pixels.
 *
 * @param {string} xpath - The XPath selector for the element to click
 * @returns {Object} Result object with success status, coordinates, and message
 */
function clickElement(xpath) {
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

  // Scroll element into view to ensure it's visible in viewport
  element.scrollIntoView({ behavior: "instant", block: "center" });

  const rect = element.getBoundingClientRect();

  const centerX = rect.left + rect.width / 2;
  const centerY = rect.top + rect.height / 2;

  return {
    success: true,
    x: centerX,
    y: centerY,
    message: "Coordinates calculated successfully",
    elementInfo: {
      width: rect.width,
      height: rect.height,
      left: rect.left,
      top: rect.top,
      text: element.innerText,
    },
  };
}

// Export the function - when used in browser automation, wrap with IIFE and pass xpath
// (() => {
//     const xpath = '{XPATH_PLACEHOLDER}';
//     return clickElement(xpath);
// })();
