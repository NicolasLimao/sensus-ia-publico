import "@testing-library/jest-dom/vitest";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

// O React 19 exige o sinal explícito para que act(...) funcione fora do navegador.
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// O jsdom não implementa scrollIntoView; é lacuna do ambiente, não do componente.
Element.prototype.scrollIntoView = () => {};
