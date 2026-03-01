import { motion } from "framer-motion";
import { BringBackTheAssistantLogo } from "./BringBackTheAssistantLogo";
import { ArrowRight } from "lucide-react";

const cardVariants = {
  hidden: { opacity: 0, y: 30 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: 0.6 + i * 0.15, duration: 0.5, ease: "easeOut" as const },
  }),
};

const HeroSection = () => {
  return (
    <section className="relative ds-wave-overlay min-h-[85vh] flex flex-col items-center justify-center px-4 pt-24 pb-16 bg-background">
      <div className="relative z-10 flex flex-col items-center gap-8">
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
        >
          <BringBackTheAssistantLogo variant="large" className="text-7xl md:text-8xl lg:text-[7rem]" />
        </motion.div>

        <motion.p
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3, duration: 0.6 }}
          className="text-2xl md:text-3xl font-light text-foreground/70 tracking-widest font-display"
        >
          Into the unknown
        </motion.p>

        <div className="flex flex-col sm:flex-row gap-5 mt-10 w-full max-w-2xl px-4">
          {[
            {
              href: "/chat",
              title: "Start Now",
              desc: "Free access to BringBackTheAssistant-V3.2.\nExperience the intelligent model.",
            },
            {
              href: "#",
              title: "Access API",
              desc: "Build with the latest BringBackTheAssistant models.\nPowerful models, smooth experience.",
            },
          ].map((card, i) => (
            <motion.a
              key={card.title}
              href={card.href}
              custom={i}
              variants={cardVariants}
              initial="hidden"
              animate="visible"
              whileHover={{ y: -4, transition: { duration: 0.2 } }}
              className="flex-1 group bg-card/50 backdrop-blur-sm rounded-2xl border border-border hover:border-primary/50 p-7 transition-all duration-300 hover:shadow-[0_8px_30px_hsl(var(--haze-sky-blue)/0.25)]"
            >
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-lg font-semibold font-display text-foreground">
                  {card.title}
                </h3>
                <ArrowRight className="w-4 h-4 text-muted-foreground group-hover:text-primary group-hover:translate-x-1 transition-all duration-200" />
              </div>
              <p className="text-sm text-muted-foreground leading-relaxed whitespace-pre-line">
                {card.desc}
              </p>
            </motion.a>
          ))}
        </div>
      </div>
    </section>
  );
};

export default HeroSection;
