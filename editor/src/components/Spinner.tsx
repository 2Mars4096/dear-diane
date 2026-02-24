export default function Spinner({ size = "sm" }: { size?: "sm" | "md" }) {
  const dim = size === "md" ? "w-5 h-5" : "w-3.5 h-3.5";
  return (
    <span
      className={`inline-block ${dim} border-2 border-current border-t-transparent rounded-full animate-spin`}
      role="status"
    />
  );
}
